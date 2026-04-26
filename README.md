# GeoTools Pro — منصة أدوات GIS

منصة ويب متكاملة لمعالجة وتحليل وعرض البيانات المكانية.

## المميزات

### الصيغ المدعومة
- ✅ **File Geodatabase (.gdb)** — ارفعها كملف ZIP
- ✅ **Shapefile (.shp)** — ارفعها كملف ZIP
- ✅ **GeoPackage (.gpkg)**
- ✅ **GeoJSON (.geojson)**
- ✅ **KML (.kml)**

### الأدوات
- 🔍 **فحص ومعالجة الأخطاء** — Gaps, Overlaps, Dangles, Duplicates, Attributes
- 📐 **فحص الطوبولوجي** — فحص شامل لقواعد الطوبولوجي
- 📊 **تلخيص البيانات** — إحصائيات شاملة مع رسوم بيانية
- 🔄 **تحويل الصيغ** — تحويل بين GeoJSON, Shapefile, GeoPackage, CSV, KML
- 📏 **حساب المساحات والأطوال** — حسابات دقيقة بـ UTM
- 📋 **جدول البيانات** — عرض كامل للبيانات الوصفية

### عارض الخرائط
- خريطة تفاعلية بـ Leaflet
- عرض كل أنواع الطبقات: مضلعات، خطوط، نقاط
- Popup عند الضغط على أي عنصر
- تصدير الطبقات كـ GeoJSON

---

## التثبيت والتشغيل

### 1. المتطلبات
- Python 3.9+
- GDAL مثبت على النظام

### 2. تثبيت GDAL (مهم — لازم قبل المكتبات)

**Windows:**
```bash
# الطريقة الأسهل: استخدم OSGeo4W
# حمّل من https://trac.osgeo.org/osgeo4w/
# أو استخدم conda:
conda install -c conda-forge gdal fiona geopandas
```

**macOS:**
```bash
brew install gdal
```

**Ubuntu/Linux:**
```bash
sudo apt-get update
sudo apt-get install gdal-bin libgdal-dev python3-gdal
```

### 3. تثبيت المكتبات
```bash
cd gis-platform
pip install -r requirements.txt
```

### 4. التشغيل
```bash
python app.py
```

### 5. فتح الموقع
افتح المتصفح على:
```
http://localhost:5000
```

---

## طريقة الاستخدام

### رفع File Geodatabase (.gdb)
1. اضغط مجلد الـ .gdb في ملف ZIP:
   - **Windows:** كلك يمين على مجلد الـ .gdb → Send to → Compressed (zipped) folder
   - **Mac:** كلك يمين → Compress
2. ارفع ملف الـ ZIP في الموقع من صفحة "عارض الخرائط"
3. الموقع يقرأ كل الطبقات تلقائياً ويعرضها لك
4. اختر أي طبقة لعرضها على الخريطة

### رفع Shapefile (.shp)
1. اضغط كل ملفات الشيب فايل (.shp, .dbf, .shx, .prj) في ملف ZIP واحد
2. ارفع ملف الـ ZIP

### رفع GeoPackage أو GeoJSON
- ارفع الملف مباشرة بدون ZIP

---

## هيكل المشروع

```
gis-platform/
├── app.py              # Flask Backend — يقرأ ملفات GIS ويعالجها
├── requirements.txt    # المكتبات المطلوبة
├── README.md           # هذا الملف
├── static/
│   └── index.html      # الواجهة الأمامية (Frontend)
└── uploads/            # مجلد الملفات المرفوعة (يُنشأ تلقائياً)
```

---

## الـ API Endpoints

| Endpoint | Method | الوصف |
|---|---|---|
| `/api/upload` | POST | رفع ملف GIS وقراءة الطبقات |
| `/api/layers` | POST | جلب بيانات طبقة محددة كـ GeoJSON |
| `/api/check-errors` | POST | فحص الأخطاء (Gaps, Overlaps, Dangles...) |
| `/api/stats` | POST | إحصائيات تفصيلية للطبقة |
| `/api/convert` | POST | تحويل طبقة لصيغة أخرى وتحميلها |

---

## النشر على سيرفر

### خيار 1: VPS (DigitalOcean, AWS, etc.)
```bash
# تثبيت المتطلبات
sudo apt-get install gdal-bin libgdal-dev python3-gdal python3-pip
pip install -r requirements.txt
pip install gunicorn

# تشغيل بـ Gunicorn (للإنتاج)
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

### خيار 2: Railway أو Render
1. ارفع المشروع على GitHub
2. اربطه بـ Railway أو Render
3. أضف Buildpack لـ GDAL
4. Start Command: `gunicorn app:app`

### خيار 3: Docker
```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y gdal-bin libgdal-dev
COPY . /app
WORKDIR /app
RUN pip install -r requirements.txt
EXPOSE 5000
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "app:app"]
```

---

## التطوير المستقبلي
- [ ] دعم ملفات DWG/DXF
- [ ] أداة تقسيم الأراضي
- [ ] تحليل الشبكات
- [ ] نظام مستخدمين
- [ ] حفظ المشاريع
- [ ] دعم الصور الفضائية (Raster)
- [ ] تقارير PDF تلقائية

---

## المطور
تم بناؤه بـ ❤️ باستخدام Python, Flask, GeoPandas, Leaflet
