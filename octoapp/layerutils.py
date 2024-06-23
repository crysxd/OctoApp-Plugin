
class LayerUtils:
    
    LayerChangeCommand = "OCTOAPP_LAYER_CHANGE"
    DisableLegacyLayerCommand = "OCTOAPP_DISABLE_LAYER_MAGIC"

    @staticmethod
    def CreateLayerChangeCommand(layer):
        return LayerUtils.LayerChangeCommand + " LAYER=" + str(layer)
    
    @staticmethod
    def IsLayerChange(line):
        #      Cura                          Orca Slicer / Prusa Slicer          Kiri:moto                          Simplify3D
        return line.startswith(";LAYER:") or line.startswith(";LAYER_CHANGE") or line.startswith(";; --- layer") or line.startswith("; layer ") 