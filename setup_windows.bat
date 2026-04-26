@echo off
chcp 65001 >nul
echo.
echo ╔══════════════════════════════════════════╗
echo ║   GeoTools Pro - Windows Setup           ║
echo ╚══════════════════════════════════════════╝
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python not found! Install from https://www.python.org
    pause
    exit /b
)
echo ✅ Python found

:: Install packages
echo.
echo 📦 Installing packages...
pip install flask geopandas fiona shapely pyproj pandas openpyxl
echo.

if errorlevel 1 (
    echo.
    echo ⚠️  If GDAL/Fiona failed, try these steps:
    echo    1. Install OSGeo4W from https://trac.osgeo.org/osgeo4w/
    echo    2. Or use conda: conda install -c conda-forge geopandas fiona
    echo.
    pause
    exit /b
)

echo ✅ All packages installed!
echo.
echo 🚀 Starting GeoTools Pro...
echo    Open browser: http://localhost:5000
echo.
python app.py
pause
