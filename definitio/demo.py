"""Deterministic synthetic data; never presented as a real-world observation."""
from shapely.geometry import box, mapping, LineString


def demo_layers():
    features = []
    for row in range(5):
        for col in range(6):
            x, y = 500000 + col * 94, 2560000 + row * 102
            features.append({"type": "Feature", "geometry": mapping(box(x, y, x + 72, y + 78)),
                             "properties": {"group_code": ["A", "B", "C"][(col // 2 + row // 3) % 3],
                                            "height": 12 + ((row * 7 + col * 3) % 9) * 4,
                                            "population": 30 + ((row * 11 + col * 7) % 13) * 9}})
    plots = {"type": "FeatureCollection", "features": features}
    boundary = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(500080, 2560060, 500438, 2560380)), "properties": {"name": "分析边界"}}]}
    roads = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(LineString([(499960, 2560090 + i * 102), (500590, 2560090 + i * 102)])),
         "properties": {"name": f"横向道路 {i + 1}"}} for i in range(4)]}
    return [("示例地块", plots), ("研究边界", boundary), ("道路中心线", roads)]
