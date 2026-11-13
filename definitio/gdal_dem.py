"""Use the GDAL Utilities C API shipped with Rasterio; no second GDAL install.

Public signatures: https://gdal.org/en/stable/api/gdal_utils.html
Every handle is scoped to this call and closed before the memory files are released.
"""
from __future__ import annotations

import ctypes as ct
from functools import lru_cache
from pathlib import Path

import numpy as np
import rasterio
from rasterio.io import MemoryFile
from affine import Affine

from .model import require


@lru_cache(maxsize=1)
def library():
    # Bind the exact GDAL already loaded by Rasterio, not a system PATH DLL.
    directory = Path(rasterio.__file__).parent.parent / "rasterio.libs"
    candidates = list(directory.glob("*gdal*.dll"))
    require(len(candidates) == 1, "NOT_SUPPORTED", "当前构建未定位到 Rasterio 配套的单一 GDAL DLL；其他平台未验证")
    lib = ct.CDLL(str(candidates[0]))
    signatures = {
        "GDALOpenEx": (ct.c_void_p, [ct.c_char_p, ct.c_uint, ct.c_void_p, ct.c_void_p, ct.c_void_p]),
        "GDALClose": (None, [ct.c_void_p]),
        "GDALDEMProcessingOptionsNew": (ct.c_void_p, [ct.POINTER(ct.c_char_p), ct.c_void_p]),
        "GDALDEMProcessingOptionsFree": (None, [ct.c_void_p]),
        "GDALDEMProcessing": (ct.c_void_p, [ct.c_char_p, ct.c_void_p, ct.c_char_p, ct.c_char_p, ct.c_void_p, ct.POINTER(ct.c_int)]),
    }
    for name, (result, args) in signatures.items():
        function = getattr(lib, name); function.restype = result; function.argtypes = args
    return lib


def terrain(verb, p, dataset, values):
    lib = library()
    operation = verb.removeprefix("gis_")
    args = ["-of", "GTiff", "-alg", "Horn"]
    if operation == "slope": args += ["-s", str(1/p["z_factor"])]
    if operation == "hillshade": args += ["-s", "1", "-z", str(p["z_factor"]), "-az", str(p["azimuth"]), "-alt", str(p["altitude"])]
    strings = (ct.c_char_p * (len(args)+1))(*[s.encode("ascii") for s in args], None)
    with MemoryFile() as source, MemoryFile() as destination:
        with source.open(driver="GTiff", height=values.shape[0], width=values.shape[1], count=1,
                         dtype=dataset["raster"]["dtype"], transform=Affine(*dataset["raster"]["transform"]),
                         crs=dataset["crs"]["wkt"], nodata=float("nan")) as writer:
            writer.write(values.astype(dataset["raster"]["dtype"]), 1)
        handle = lib.GDALOpenEx(source.name.encode("utf-8"), 2, None, None, None)
        require(handle, "BACKEND_ERROR", "GDAL 无法打开内存 DEM")
        options = output = None
        try:
            options = lib.GDALDEMProcessingOptionsNew(strings, None)
            require(options, "BACKEND_ERROR", "GDAL 拒绝显式地形选项")
            usage = ct.c_int(0)
            output = lib.GDALDEMProcessing(destination.name.encode("utf-8"), handle, operation.encode("ascii"), None, options, ct.byref(usage))
            require(output and usage.value == 0, "BACKEND_ERROR", "GDAL 地形计算失败")
        finally:
            if output: lib.GDALClose(output)
            if options: lib.GDALDEMProcessingOptionsFree(options)
            lib.GDALClose(handle)
        with destination.open() as reader:
            return reader.read(1, masked=True).astype("float64").filled(np.nan)
