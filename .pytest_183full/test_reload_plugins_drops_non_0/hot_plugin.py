from core.plugins import InpaintEnginePlugin, register_engine
class _P(InpaintEnginePlugin):
    name = 'hot_engine'
    display_name = '热加载 v2'
    def inpaint(self, image, mask, *, device=None, progress=None):
        return image
register_engine(_P)
