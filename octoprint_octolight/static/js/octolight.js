$(function() {
    function OctolightViewModel(parameters){
    	self.psu_indicator = $("#light_indicator");
    	self.isLightOn = ko.observable(undefined);

    	self.onDataUpdaterPluginMessage = function(plugin, data) {
            if (plugin != "octolight") {
                return;
            }

            if (data.isLightOn !== undefined) {
                self.isLightOn(data.isLightOn);
            }
        };

        self.onStartup = function () {
            self.isLightOn.subscribe(function() {
            	console.log(self.isLightOn());
                if (self.isLightOn()) {
                    self.light_indicator.removeClass("off").addClass("on");
                } else {
                    self.light_indicator.removeClass("on").addClass("off");
                }
            });
        }
    }
});