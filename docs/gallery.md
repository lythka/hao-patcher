# Gallery

macOS 27.2.1 (26B131) on the development platform, a 2012 MacBook Pro (Intel HD 4000), 2026-11-11 and 11-12. Weather and
WebGPU need Metal argument buffers and ASTC textures, which the HD 4000's legacy Metal stack does not provide;
[tools/metal-argbuf](../tools/metal-argbuf) implements them in the shader compiler and the driver. Maps and SceneKit
need their shader libraries repaired for the old compiler ([cache/metallib](../cache/metallib)).

## The desktop

About This Mac, Control Center and the desktop widgets (the Wi-Fi network name is hidden):

![About This Mac, Control Center and widgets on a 2012 MacBook Pro running macOS 27.2.1](images/gallery/about-this-mac.jpg)

## Safari

apple.com:

![Safari showing apple.com](images/gallery/safari-apple.jpg)

YouTube in 720p60 with hardware video decoding (Stats for nerds: 3 dropped frames of 2602):

![Safari playing a YouTube video with the Stats for nerds overlay](images/gallery/safari-youtube.jpg)

## Weather

![Weather: Los Angeles conditions, air quality, wind and 10-day forecast](images/gallery/weather.jpg)

The temperature, wind and air-quality maps:

![Weather temperature map of the US West Coast](images/gallery/weather-temperature-map.jpg)

![Weather wind map with animated wind streaks](images/gallery/weather-wind-map.jpg)

![Weather air-quality map](images/gallery/weather-air-quality-map.jpg)

## Maps

Satellite imagery with 3D buildings, San Francisco:

![Maps satellite view of downtown San Francisco with 3D buildings](images/gallery/maps-3d-buildings.jpg)

## WebGPU in Safari

A compute shader and a render pass, read back and checked
([probes/webgpu-check.html](../tools/metal-argbuf/probes/webgpu-check.html)):

![Safari running a WebGPU compute and render check on the HD 4000](images/gallery/safari-webgpu.jpg)

## SceneKit

A small SceneKit test scene: physically based materials, a cube-map environment (reflected in the chrome
sphere), shadows and floor reflections:

![SceneKit scene with a box, a chrome sphere and a torus on a reflective floor](images/gallery/scenekit.jpg)
