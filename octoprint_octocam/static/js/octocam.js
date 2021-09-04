$(function() {
    function OctoCamViewModel(parameters){
    	var self = this;

        self.settingsViewModel = parameters[0]
        self.loginState = parameters[1];

    	self.light_indicator = $("#light_indicator");
    	self.isLightOn = ko.observable(undefined);

        self.onBeforeBinding = function() {
            self.settings = self.settingsViewModel.settings;
        };

    	self.onDataUpdaterPluginMessage = function(plugin, data) {
            if (plugin != "octocam") {
                return;
            }

            if (data.torchOn !== undefined) {
                self.isLightOn(data.torchOn);
            }
        };

        self.onStartup = function () {
            self.isLightOn.subscribe(function() {
                if (self.isLightOn()) {
                    self.light_indicator.removeClass("off").addClass("on");
                } else {
                    self.light_indicator.removeClass("on").addClass("off");
                }
            });
        }
    }

     OCTOPRINT_VIEWMODELS.push({
        construct: OctoCamViewModel,
        dependencies: ["settingsViewModel","loginStateViewModel"],
        elements: ["#navbar_plugin_octocam","#settings_plugin_octocam"]
    });
});
