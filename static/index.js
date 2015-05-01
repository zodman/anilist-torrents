var app = angular.module("app", ["angular-locker"]);

// Wow, this is incredibly stupid. Way to go Angular!
// https://github.com/angular/angular.js/issues/6039
app.config(function ($httpProvider, lockerProvider) {
  $httpProvider.defaults.headers.post["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8";
  $httpProvider.defaults.transformRequest = function (d) {
    if (d === undefined) return d;
    var s = [];
    for (var p in d) s.push(encodeURIComponent(p) + "=" + encodeURIComponent(d[p]));
    return s.join("&");
  };

  lockerProvider.setDefaultNamespace(false);
});

app.controller("MainController", function ($scope, $http, locker) {
  locker.bind($scope, "only_airing");
  locker.bind($scope, "only_trusted");

  $scope.loading = true;
  $http.get("/api/user").success(function (data) {
    $scope.loading = false;
    $scope.user = data;
    _.each($scope.user.list, function (show, index) {

        
      // Compute title for each show based on user preferences
      Object.defineProperty(show.anime, "title", {
        get: function () {
          return this["title_" + $scope.user.title_language];
        }
      });


      // Compute group for each show based on "notes", which is the only state we can manipulate
      Object.defineProperty(show, "group", {
        get: function () {
 
          var group = /^\[(.*?)\]/.exec(this.notes);
          if (!group) {
 
            return null;
          }
            

          found = _.find(this.groups, "name", group[1]);
          
          return found
        },
        set: function (group) {
          var notes = /^(?:\[(.*?)\])?(.*)/.exec(this.notes || "")[2];
          if (group && group.name) notes = "[" + group.name + "]" + notes;
          if (!notes) notes = "\u200B"; // Force it to be SOMETHING
          this.notes = notes;

          // Persist the changes upstream
          $http.post("/api/notes", {
            anime: this.anime.id,
            notes: this.notes
          });
        }
      });

      show.loading = true;
      // Let the DOM catch up...
      setTimeout(function () {
        $http.get("/api/show/" + show.anime.id + "/torrents").success(function (data) {
          show.loading = false;
          show.groups = _.map(data, function (episodes, name) {
            var group = {
              show: show, // Ensure groups have a reference to it's parent
              name: name,
              downloads: _.sum(episodes, "downloads"),
              a_plus: _.any(episodes, "a_plus"),
              trusted: _.any(episodes, "trusted"),
              remake: _.any(episodes, "remake"),
            };
            // Ensure episodes have references to their parent group & show
            group.episodes = _.map(episodes, function (episode) {
              episode.show = show;
              episode.group = group;
              return episode;
            });
            return group;
          });
        });
      }, 40);
    });
  }).error(function () {
    $scope.loading = false;
    // Login screen will automatically appear
  });

  $scope.unwatched = function (value, index, arr) {
    console.log("test",value.episode, value.show.episodes_watched, value.episode > value.show.episodes_watched);
    return true;
    return value.episode > value.show.episodes_watched;
  };

  $scope.no_groups = function (show) {
    return !show.loading && !show.groups.filter($scope.acceptable_group).length;
  };

  $scope.no_episodes = function (show) {
    return !show.loading && show.group && show.group.episodes.filter($scope.unwatched).length === 0;
  };

  $scope.acceptable_show = function (show) {
    return !$scope.only_airing || show.anime.airing_status === "currently airing";
  };

  $scope.acceptable_group = function (group) {
    return !$scope.only_trusted || group.trusted;
  };
});
