# -*- coding: utf-8 -*-

import datetime
import json
import os
import re
import time
import urllib
from functools import wraps
from xml.sax.saxutils import escape

import requests
import requests_cache

from beaker.cache import CacheManager
from beaker.middleware import SessionMiddleware
from beaker.util import parse_cache_config_options
from bs4 import BeautifulSoup

from bottle import app as app_factory
from bottle import (abort, delete, get, hook, post, redirect, request,
                    response, run, static_file)

# Override Synonyms for when Anilist is just being dumb
SYNONYMS = {
    20678: ["Shinmai Maou No Testament"],
    20762: ["Knights of Sidonia: The Ninth Planet Crusade"],
}

# Initialize app
ACCESS_TOKEN = None
ACCESS_EXPIRES = 0
with open("config.json", "r") as f:
    CONFIG = json.loads(f.read())
with open("fansub.json", "r") as f:
    fansubs = json.loads(f.read())

with open("ignores.json", "r") as f:
    ignores = json.loads(f.read())


requests_cache.install_cache('animelistcache',backend=CONFIG.setdefault("request_backend","sqlite"), expire_after=1600)
app = SessionMiddleware(app_factory(), {
    "session.type": "cookie", # Store everything in a cookie
    "session.auto": True, # Auto-save the session (we would do so anyway since it's a cookie)
    "session.cookie_expires": False, # Never log a user out
    "session.key": "session", # So imaginative
    "session.secret": CONFIG["session_secret"],
    "session.validate_key": CONFIG["session_secret"]
   # 'session.type': 'ext:memcached',
   # 'session.url': '127.0.0.1:11211',
})
cache = CacheManager(**parse_cache_config_options({
    "cache.type": "file",
    "cache.data_dir": "./data",
    "cache.lock_dir": "./lock",
    "cache.expire": 3600
}))

@hook("before_request")
def setup_request():
    request.session = request.environ.get("beaker.session")

# Turns kwargs into args, given a list of expected kwargs
# We use this to convert bottle-style routes into beaker-style functions
def force_positional_route(*args):
    def wrapper(func):
        @wraps(func)
        def wrapped(**kwargs):
            return func(*[kwargs.get(a, None) for a in args])
        return wrapped
    return wrapper

# Utility functions
def ensure_current_access_token():
    global ACCESS_TOKEN, ACCESS_EXPIRES
    if time.time() >= ACCESS_EXPIRES - 30: # Ensure we have 30 seconds of leeway
        r = requests.post("https://anilist.co/api/auth/access_token", {
            "grant_type": "client_credentials",
            "client_id": CONFIG["client_id"],
            "client_secret": CONFIG["client_secret"]
        }, verify=False)
        data = r.json()
        ACCESS_TOKEN = data["access_token"]
        ACCESS_EXPIRES = data["expires"]
    print ACCESS_TOKEN

def get_access_token():
    print request.session
    if not ("access_token" in request.session and "refresh_token" in request.session and "expires" in request.session):
        return None
    if time.time() >= request.session["expires"] - 30: # Ensure we have 30 seconds of leeway
        if not request.session["refresh_token"]:
            return None


        with requests_cache.disabled():
            r = requests.post("https://anilist.co/api/auth/access_token", {
                "grant_type": "refresh_token",
                "client_id": CONFIG["client_id"],
                "client_secret": CONFIG["client_secret"],
                "refresh_token": request.session["refresh_token"]
            }, verify=False)
            data = r.json()
            if "error" in data and data["error"]=="invalid_request":
                return None
            request.session["access_token"] = data["access_token"]
            request.session["expires"] = data["expires"]
    return request.session["access_token"]

def search_names(name):
    name = name.replace("(TV)","") # No group includes this
    name = name.replace(".", " ") # Groups might have a space instead
    name = name.replace("?", "") # Common to drop end punctuation
    name = name.replace(" o ", " ") # "o" vs "wo"? just ignore the word instead

    # Strip numbers out, but only as a fallback
    # Also if there was a number, quote it, otherwise it'll match episode numbers
    name2 = re.sub(r"\b\d+\b", "", name)
    quote = name != name2 or "!" in name
    names = [u'"{}"'.format(name)] if quote else [name]
    if name != name2:
        names.append(name2)

    return [re.sub(r"\s+", " ", n).strip() for n in names]

def anilist(method, url, **kwargs):
    ensure_current_access_token()

    url = "https://anilist.co/api/" + url
    kwargs["headers"] = kwargs.get("headers", {})
    kwargs["headers"]["Authorization"] = "Bearer " + (get_access_token() or ACCESS_TOKEN)
    kwargs["verify"] = False

    return requests.request(method, url, **kwargs)

# Web app
@get("/static/<filename:path>")
def static(filename):
    return static_file(filename, root="{}/static".format(os.getcwd()))

@get("/")
def index():
    return static_file("index.html", root=os.getcwd())

@get("/login")
def login():
    with requests_cache.disabled():
        code = request.params.code
        if code:
            
            r = requests.post("https://anilist.co/api/auth/access_token", {
                    "grant_type": "authorization_code",
                    "client_id": CONFIG["client_id"],
                    "client_secret": CONFIG["client_secret"],
                    "redirect_uri": CONFIG["base_url"] + "/login",
                    "code": code
                }, verify=False)
            data = r.json()
            print repr(data)
            request.session["access_token"] = data["access_token"]
            request.session["refresh_token"] = data["refresh_token"]
            request.session["expires"] = data["expires"]

        if get_access_token():
            redirect("/")
        else:
            redirect("https://anilist.co/api/auth/authorize?" + urllib.urlencode({
                "grant_type": "authorization_code",
                "response_type": "code",
                "client_id": CONFIG["client_id"],
                "redirect_uri": CONFIG["base_url"] + "/login"
            }))

@get("/logout")
def logout():
    if get_access_token():
        del request.session["access_token"]
        del request.session["refresh_token"]
        del request.session["expires"]
    redirect("/")

# API
@get("/api/impersonate/<access_token>/<expires:int>")
def impersonate(access_token, expires):
    request.session["access_token"] = access_token
    request.session["refresh_token"] = None
    request.session["expires"] = expires
    redirect("/")

@get("/api/user")
def current_user():
    if not get_access_token():
        abort(401)
    r = anilist("GET", "user")
    user = r.json()
    r = anilist("GET", "user/{}/animelist".format(user["id"]))
    d = r.json()
    user["list"] = d["lists"].get("watching", []) + d["lists"].get("plan to watch", [])
    return json.dumps(user)

@post("/api/notes")
def update_notes():
    if not get_access_token():
        abort(401)
    anime, notes = request.params.anime, request.params.notes
    if not anime:
        abort(400)
    r = anilist("PUT", "animelist", data={"id": anime, "notes": notes})
    abort(r.status_code, r.text)

@get("/api/show/<show_id>/torrents")
@force_positional_route("show_id")
@cache.cache()
def show_torrents(show_id):
    r = anilist("GET", "anime/{}/page".format(show_id))
    data = r.json()

    # OK, this is a little complicated...
    # Each show has multiple names, so we want to search for all of them at the same time
    # since we don't know which name fansubbers will use...
    # However, sometimes sequels have numbers in the name
    # and fansubbers just decide "fuck it, let's just keep incrementing the old name"
    # So you end up with "1-12" as "XXX" and "13-24" as "XXX 2", but searching for "XXX 2" gets no results and GODDAMMIT
    # Hence we're going to try to search for "XXX 2" and if that doesn't work we'll search for "XXX" and subtract
    # the total_episodes for "XXX" from what we get for those results and pray that we did the right thing
    #
    # ;_;
    names = [data["title_english"], data["title_romaji"], data["title_japanese"]] + data["synonyms"] + SYNONYMS.get(data["id"], [])
    names = zip(*[search_names(name) for name in names]) # Cleans cruft out of the name
    queries = zip(names, [False]+[True]*(len(names)-1))

    fallback_fix = 0
    for r in data["relations"]:
        if r["relation_type"] == "prequel":
            fallback_fix += r["total_episodes"]
    fansub="|".join(fansubs)
    exclude = "-".join(ignores)
    torrents = {}

    for terms, fallback in queries:
        print terms
        for term in set(terms):
            offset = 0
            term = term + "+"+ fansub
            while True:
                offset += 1
                r = requests.get("http://www.nyaa.se/", params={
                        "page": "rss", 
                        "cats": "1_38", 
                        "term": term, 
                        "minage":"",
                        "maxage":"",
                        "minsize":"",
                        "maxsize":"",
                        "offset": offset})
                print r.url
                items = BeautifulSoup(r.text, "xml").find_all("item")
                if not items:
                    break
                for t in items:
                    d = {
                        "name": t.title.string,
                        "group": None,
                        "info": t.guid.string,
                        "download": t.link.string,
                        "uploaded": datetime.datetime.strptime(t.pubDate.string, "%a, %d %b %Y %H:%M:%S +0000").isoformat(" ")
                    }
                    # group = /^[(.*?)]/.match(name)
                    if d["name"].startswith("[") and "]" in d["name"]:
                        d["group"] = d["name"][1:d["name"].index("]")]
                        m = re.search(r"\[(480|720|1080)[pP]?\]", d["name"])
                        if m:
                            d["group"] += " {}p".format(m.group(1))

                    # episode = last set of numbers we find preceded by not a v
                    filtered_name = re.sub(r"\[[^]]*\]", "", re.sub(r"\([^)]*\)", "", d["name"]))
                    filtered_name = filtered_name.rpartition(".")[0] # Remove extension
                    ints = re.findall(r"(?<!v)\d+", filtered_name)
                    d["episode"] = int(ints[-1]) if ints else None
                    if fallback and d["episode"]:
                        d["episode"] -= fallback_fix
                        if d["episode"] <= 0:
                            continue

                    metadata = t.description.string.split(" - ")
                    metadata[0] = [int(x.split(" ")[0]) for x in metadata[0].split(", ")]
                    d["seeders"] = metadata[0][0]
                    d["leechers"] = metadata[0][1]
                    d["downloads"] = metadata[0][2]
                    d["size"] = metadata[1]
                    d["remake"] = "Remake" in metadata[2:]
                    d["trusted"] = "Trusted" in metadata[2:]
                    d["a_plus"] = "A+" in metadata[2:]

                    torrents[d["group"]] = torrents.get(d["group"], {})
                    torrents[d["group"]][d["episode"]] = d

            if offset > 10:
                print "!!! WARNING !!! - Extremely high offset for {!r} = {:d}".format(term, offset)

        torrents.pop(None, None)
        if torrents:
            break

    for k, v in torrents.items():
        torrents[k] = sorted(v.values(), key=lambda i: i["uploaded"], reverse=True)

    return json.dumps(torrents)

@delete("/api/show/<show_id>/torrents")
def invalidate_show_torrents(show_id):
    cache.invalidate(show_torrents, show_id)
    return ""

@get("/api/user/<user_id>/rss")
@force_positional_route("user_id")
@cache.cache()
def user_rss(user_id):
    r = anilist("GET", "user/{}/animelist".format(user_id))
    user = r.json()
    shows = user["lists"].get("watching", []) + user["lists"].get("plan to watch", [])
    torrents = []

    for show in shows:
        m = re.match(r"\[(.*?)\]", show["notes"] or "")
        group = m.group(1) if m else None
        if not group:
            continue

        r = requests.get("{}/api/show/{:d}/torrents".format(CONFIG["base_url"], show["anime"]["id"]))
        d = r.json()

        if group not in d:
            continue
        for torrent in d[group]:
            if torrent["episode"] > show["episodes_watched"]:
                torrents.append(torrent)

    result = '<rss xmlns:atom="http://www.w3.org/2005/Atom" version="2.0">'
    result += '<channel>'
    result += '<title>Anilist Torrents for {}</title>'.format(escape(user["display_name"].encode("UTF-8")))
    result += '<link>{}</link>'.format(escape(CONFIG["base_url"]))
    result += '<atom:link href="{}" rel="self" type="application/rss+xml" />'.format("{}/api/user/{:d}/rss".format(CONFIG["base_url"], user["id"]))
    result += '<description />'
    result += '<ttl>60</ttl>'

    for t in sorted(torrents, key=lambda i: i["uploaded"], reverse=True):
        result += '<item>'
        result += '<title>{}</title>'.format(escape(t["name"].encode("UTF-8")))
        result += '<link>{}</link>'.format(escape(t["download"].encode("UTF-8")))
        result += '<guid>{}</guid>'.format(escape(t["info"].encode("UTF-8")))
        result += '<pubDate>{}</pubDate>'.format(escape(t["uploaded"].encode("UTF-8")))
        result += '<description><![CDATA[ {} ]]></description>'.format(json.dumps(t))
        result += '</item>'

    result += '</channel>'
    result += '</rss>'

    response.content_type = "application/rss+xml; charset=utf-8"
    return result

@delete("/api/user/<user_id>/rss")
def invalidate_user_rss(user_id):
    cache.invalidate(user_rss, user_id)
    return ""

if __name__ == "__main__":
    run(app=app, host="127.0.0.1", port=os.environ.get("PORT", 8080), debug=True, reloader=True, server="gunicorn")
