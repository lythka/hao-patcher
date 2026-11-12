# Gallery

macOS 27.2.1 (26B131) on the development platform, a 2012 MacBook Pro (Intel HD 4000), 2026-11-12. These views
need Metal argument buffers and ASTC textures, which the HD 4000's legacy Metal stack does not provide;
[tools/metal-argbuf](../tools/metal-argbuf) implements them in the shader compiler and the driver.

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
