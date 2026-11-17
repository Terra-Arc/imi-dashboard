import logging
import os
from importlib.metadata import PackageNotFoundError, version

# netCDF4's Windows wheels from 1.7.4 on bundle HDF5 compression plugins and,
# on import, point HDF5_PLUGIN_PATH at them. HDF5 then searches that folder
# and loads its DLLs on every check of a variable's filters, which xarray
# makes for each variable it opens: about 30-60 ms each, so a three-period
# sample took 13 s instead of 0.25 s. The plugins add nothing the converter
# needs (IMI writes zlib, built into HDF5), and with 1.7.4 to 1.7.5.1 on
# Windows they do not even work (zstd, bzip2 and blosc fail with or without
# them). An empty path, set before netCDF4 is imported, restores the
# behaviour of 1.7.3 and earlier. A value the user set is left alone, and so
# is every other platform, where the plugin search is cheap.
if os.name == "nt" and "HDF5_PLUGIN_PATH" not in os.environ:
    os.environ["HDF5_PLUGIN_PATH"] = ""

# Set by setuptools-scm from the git tag when the package is built or
# installed; an editable install keeps the value from its install time.
try:
    __version__ = version("imi-dashboard")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"

# A library attaches no output of its own (the logging HOWTO, "Configuring
# Logging for a Library"): without this, a program that calls the package
# without configuring logging would get any WARNING record printed by
# Python's last-resort handler. The command line attaches its own handlers.
logging.getLogger(__name__).addHandler(logging.NullHandler())
