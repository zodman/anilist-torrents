var app = angular.module("app", []);

// Wow, this is incredibly stupid. Way to go Angular!
// https://github.com/angular/angular.js/issues/6039
app.config(function ($httpProvider) {
  $httpProvider.defaults.headers.post["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8";
  $httpProvider.defaults.transformRequest = function (d) {
    if (d === undefined) return d;
    var s = [];
    for (var p in d) s.push(encodeURIComponent(p) + "=" + encodeURIComponent(d[p]));
    return s.join("&");
  };
});

app.controller("MainController", function ($scope, $http) {
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
          if (!group) return null;

          return _.find(this.groups, "name", group[1]);
        },
        set: function (group) {
          var notes = /^(?:\[(.*?)\])?(.*)/.exec(this.notes || "")[2];
          if (group && group.name) notes = "[" + group.name + "]" + notes;
          if (!notes) notes = " "; // Force it to be SOMETHING
          this.notes = notes;

          // Persist the changes upstream
          $http.post("/api/notes", {
            anime: this.anime.id,
            notes: this.notes
          });
        }
      });

      show.loading = true;
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
    });
  }).error(function () {
    $scope.loading = false;
    // Login screen will automatically appear
  });

  $scope.unwatched = function (value, index, arr) {
    return value.episode > value.show.episodes_watched;
  };

  $scope.no_groups = function (show) {
    return !show.loading && !show.groups.length;
  };

  $scope.no_episodes = function (show) {
    return !show.loading && show.group && show.group.episodes.filter($scope.unwatched).length === 0;
  };
});
