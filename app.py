"""
GeoTools Pro — Flask Backend
✅ متوافق مع Windows
"""

from flask import Flask, request, jsonify, send_from_directory
import fiona
import geopandas as gpd
import json
import os
import uuid
import zipfile
import tempfile
import traceback
from pathlib import Path
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder='static')

# ===== CORS headers يدوياً (بدون flask-cors) =====
@app.after_request
def add_cors(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
    return response

app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


@app.route('/')
def index():
    # Try static folder first, then root
    if os.path.exists(os.path.join('static', 'index.html')):
        return send_from_directory('static', 'index.html')
    elif os.path.exists('index.html'):
        return send_from_directory('.', 'index.html')
    else:
        return "index.html not found", 404


@app.route('/api/upload', methods=['POST', 'OPTIONS'])
def upload_file():
    if request.method == 'OPTIONS':
        return '', 204

    print("\n📂 === Upload Request ===")

    try:
        if 'file' not in request.files:
            print(f"❌ No 'file' key. Keys: {list(request.files.keys())}")
            return jsonify({'error': 'لم يتم رفع أي ملف'}), 400

        file = request.files['file']
        if not file or file.filename == '':
            return jsonify({'error': 'لم يتم اختيار ملف'}), 400

        original_name = file.filename
        print(f"📄 File: {original_name}")

        upload_id = str(uuid.uuid4())[:8]
        upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], upload_id)
        os.makedirs(upload_dir, exist_ok=True)

        # secure_filename يحذف الأحرف العربية — نستخدم اسم بديل
        safe_name = secure_filename(original_name)
        if not safe_name:
            ext = Path(original_name).suffix.lower() or '.zip'
            safe_name = f"upload_{upload_id}{ext}"

        filepath = os.path.join(upload_dir, safe_name)
        file.save(filepath)

        size = os.path.getsize(filepath)
        print(f"✅ Saved: {filepath} ({size/1024:.0f} KB)")

        if size == 0:
            return jsonify({'error': 'الملف فارغ'}), 400

        # === ZIP ===
        if safe_name.lower().endswith('.zip'):
            print("📦 Extracting ZIP...")
            extract_dir = os.path.join(upload_dir, 'extracted')
            os.makedirs(extract_dir, exist_ok=True)

            try:
                with zipfile.ZipFile(filepath, 'r') as zf:
                    zf.extractall(extract_dir)
            except zipfile.BadZipFile:
                return jsonify({'error': 'ملف ZIP تالف'}), 400

            # Print contents
            for root, dirs, files in os.walk(extract_dir):
                for f in files:
                    print(f"   {os.path.relpath(os.path.join(root, f), extract_dir)}")

            filepath = find_gis_file(extract_dir)
            if not filepath:
                return jsonify({'error': 'ما لقيت ملفات GIS داخل الـ ZIP. تأكد من ضغط مجلد الـ .gdb كامل'}), 400

        print(f"🔍 Reading: {filepath}")
        result = read_gis_file(filepath)
        print(f"✅ {result['total_layers']} layers found")
        return jsonify(result)

    except Exception as e:
        print(f"❌ {e}\n{traceback.format_exc()}")
        return jsonify({'error': f'خطأ: {e}'}), 500


@app.route('/api/layers', methods=['POST'])
def get_layer_data():
    data = request.json
    filepath = data.get('filepath')
    layer_name = data.get('layer')

    try:
        gdf = gpd.read_file(filepath, layer=layer_name)
        if gdf.crs and str(gdf.crs) != 'EPSG:4326':
            gdf = gdf.to_crs('EPSG:4326')

        return jsonify({
            'geojson': json.loads(gdf.to_json()),
            'stats': get_layer_stats(gdf),
            'crs': str(gdf.crs) if gdf.crs else 'Unknown',
            'feature_count': len(gdf),
            'geometry_type': gdf.geometry.geom_type.unique().tolist(),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/check-errors', methods=['POST'])
def check_errors():
    data = request.json
    filepath = data.get('filepath')
    layer_name = data.get('layer')
    checks = data.get('checks', {})

    try:
        gdf = gpd.read_file(filepath, layer=layer_name)
        errors = []
        import math
        from shapely.ops import unary_union, polygonize
        from shapely.geometry import MultiLineString, MultiPolygon, mapping

        # Create a WGS84 version for location reporting
        gdf_wgs84 = None
        try:
            if gdf.crs and str(gdf.crs) != 'EPSG:4326':
                gdf_wgs84 = gdf.to_crs(epsg=4326)
                print(f"   CRS: {gdf.crs} -> converted to WGS84 for map")
            else:
                gdf_wgs84 = gdf
                print(f"   CRS: WGS84 (no conversion needed)")
        except:
            gdf_wgs84 = gdf
            print(f"   CRS conversion failed, using original")

        shape_type = gdf.geometry.geom_type.iloc[0] if len(gdf) > 0 else 'Unknown'
        print(f"   Layer: {layer_name}, Type: {shape_type}, Features: {len(gdf)}")

        # ============ 1. OVERLAPS (ArcPy: Intersect -> Dissolve) ============
        if checks.get('overlaps', True) and len(gdf) > 1:
            print("   Checking overlaps...")
            polys = gdf[gdf.geometry.geom_type.isin(['Polygon', 'MultiPolygon'])]
            if len(polys) > 0:
                overlap_pairs = []
                overlap_geoms = []
                limit = min(len(polys), 200)
                for i in range(limit):
                    for j in range(i + 1, limit):
                        try:
                            g1 = polys.geometry.iloc[i]
                            g2 = polys.geometry.iloc[j]
                            if g1.intersects(g2):
                                inter = g1.intersection(g2)
                                # Only count area overlaps (not touching edges)
                                if not inter.is_empty and inter.area > 1e-12:
                                    overlap_pairs.append((int(polys.index[i]), int(polys.index[j])))
                                    overlap_geoms.append(inter)
                        except: pass
                # Merge into connected groups
                if overlap_pairs:
                    groups = []
                    group_geoms = []
                    for k, (a, b) in enumerate(overlap_pairs):
                        merged = False
                        for gi, g in enumerate(groups):
                            if a in g or b in g:
                                g.add(a); g.add(b)
                                group_geoms[gi].append(overlap_geoms[k])
                                merged = True; break
                        if not merged:
                            groups.append({a, b})
                            group_geoms.append([overlap_geoms[k]])
                    changed = True
                    while changed:
                        changed = False
                        for i in range(len(groups)):
                            for j in range(i + 1, len(groups)):
                                if groups[i] & groups[j]:
                                    groups[i] |= groups[j]
                                    group_geoms[i].extend(group_geoms[j])
                                    groups.pop(j); group_geoms.pop(j)
                                    changed = True; break
                            if changed: break
                    for gi, grp in enumerate(groups):
                        flist = ", ".join([str(f) for f in sorted(grp)])
                        # Get centroid of overlap area in WGS84
                        try:
                            combined = unary_union(group_geoms[gi])
                            # Transform to WGS84
                            if gdf_wgs84 is not gdf:
                                import pyproj
                                from shapely.ops import transform
                                project = pyproj.Transformer.from_crs(gdf.crs, 'EPSG:4326', always_xy=True).transform
                                combined_wgs = transform(project, combined)
                            else:
                                combined_wgs = combined
                            c = combined_wgs.centroid
                            loc = {"lat": c.y, "lng": c.x, "geojson": mapping(combined_wgs)}
                        except:
                            loc = None
                        errors.append({"type": "Overlap", "severity": "High",
                            "desc": f"Overlap group: features {flist}", "location": loc})
                print(f"   Overlaps: {len(overlap_pairs)} pairs -> {len([e for e in errors if e['type']=='Overlap'])} groups")

        # ============ 2. GAPS (ArcPy: FeatureToPolygon -> Erase) ============
        if checks.get('gaps', True) and len(gdf) > 1:
            print("   Checking gaps...")
            polys = gdf[gdf.geometry.geom_type.isin(['Polygon', 'MultiPolygon'])]
            if len(polys) > 1:
                try:
                    # Same logic as ArcPy FeatureToPolygon -> Erase
                    # 1. Get all polygon boundaries as lines
                    all_boundaries = []
                    for geom in polys.geometry:
                        if geom.geom_type == 'MultiPolygon':
                            for poly in geom.geoms:
                                all_boundaries.append(poly.boundary)
                        else:
                            all_boundaries.append(geom.boundary)
                    
                    # 2. Polygonize all boundaries (like FeatureToPolygon)
                    merged_lines = unary_union(all_boundaries)
                    filled_polys = list(polygonize(merged_lines))
                    
                    # 3. Erase original polygons from filled (finds gaps)
                    if filled_polys:
                        filled_union = unary_union(filled_polys)
                        original_union = unary_union(polys.geometry.tolist())
                        gaps_geom = filled_union.difference(original_union)
                        
                        gap_count = 0
                        if not gaps_geom.is_empty:
                            gap_parts = []
                            if hasattr(gaps_geom, 'geoms'):
                                gap_parts = list(gaps_geom.geoms)
                            else:
                                gap_parts = [gaps_geom]
                            
                            for gap in gap_parts:
                                if gap.area > 1e-12:
                                    gap_count += 1
                                    area_sqm = gap.area * 111320 * 111320
                                    try:
                                        if gdf_wgs84 is not gdf:
                                            import pyproj
                                            from shapely.ops import transform
                                            project = pyproj.Transformer.from_crs(gdf.crs, 'EPSG:4326', always_xy=True).transform
                                            gap_wgs = transform(project, gap)
                                        else:
                                            gap_wgs = gap
                                        c = gap_wgs.centroid
                                        loc = {"lat": c.y, "lng": c.x, "geojson": mapping(gap_wgs)}
                                    except:
                                        loc = None
                                    if gap_count <= 50:
                                        errors.append({"type": "Gap", "severity": "Medium",
                                            "desc": f"Gap #{gap_count} (area: {area_sqm:.1f} sqm)", "location": loc})
                        
                        if gap_count > 50:
                            errors.append({"type": "Gap", "severity": "Medium",
                                "desc": f"... and {gap_count - 50} more gaps"})
                        print(f"   Gaps found: {gap_count}")
                except Exception as e:
                    print(f"   Gap check error: {e}")

        # ============ 3. DANGLES (ArcPy: FeatureVerticesToPoints DANGLE) ============
        if checks.get('dangles', True):
            print("   Checking dangles...")
            lines_gdf = gdf[gdf.geometry.geom_type.isin(['LineString', 'MultiLineString'])]
            lines_wgs = gdf_wgs84[gdf_wgs84.geometry.geom_type.isin(['LineString', 'MultiLineString'])] if gdf_wgs84 is not None else lines_gdf
            if len(lines_gdf) > 0:
                # Collect all endpoints
                endpoints = []
                endpoints_wgs = []
                for idx, row in lines_gdf.iterrows():
                    try:
                        geom = row.geometry
                        parts = list(geom.geoms) if geom.geom_type == 'MultiLineString' else [geom]
                        # WGS84 version for display
                        try:
                            geom_w = lines_wgs.loc[idx].geometry
                            parts_w = list(geom_w.geoms) if geom_w.geom_type == 'MultiLineString' else [geom_w]
                        except:
                            parts_w = parts
                        for pi, part in enumerate(parts):
                            coords = list(part.coords)
                            coords_w = list(parts_w[pi].coords) if pi < len(parts_w) else coords
                            if len(coords) >= 2:
                                endpoints.append((idx, "start", coords[0]))
                                endpoints_wgs.append(coords_w[0])
                                endpoints.append((idx, "end", coords[-1]))
                                endpoints_wgs.append(coords_w[-1])
                    except: pass

                # A dangle = endpoint that only connects to one line (no other endpoint nearby)
                tolerance = 1e-6
                dangle_count = 0
                for i, (idx, etype, pt) in enumerate(endpoints):
                    connections = 0
                    for j, (idx2, etype2, pt2) in enumerate(endpoints):
                        if i != j:
                            dist = ((pt[0]-pt2[0])**2 + (pt[1]-pt2[1])**2)**0.5
                            if dist < tolerance:
                                connections += 1
                    if connections == 0:
                        dangle_count += 1
                        if dangle_count <= 100:
                            wpt = endpoints_wgs[i]
                            errors.append({"type": "Dangle", "severity": "Low",
                                "desc": f"Dangling {etype} at feature {idx}",
                                "location": {"lat": wpt[1], "lng": wpt[0]}})
                if dangle_count > 100:
                    errors.append({"type": "Dangle", "severity": "Low",
                        "desc": f"... and {dangle_count - 100} more dangles"})
                print(f"   Dangles found: {dangle_count}")

        # ============ 4. DUPLICATES ============
        if checks.get('duplicates', True):
            print("   Checking duplicates...")
            dup_count = 0
            limit = min(len(gdf), 200)
            for i in range(limit):
                for j in range(i + 1, limit):
                    try:
                        if gdf.geometry.iloc[i].equals(gdf.geometry.iloc[j]):
                            dup_count += 1
                            try:
                                c = gdf_wgs84.geometry.iloc[i].centroid
                                loc = {"lat": c.y, "lng": c.x}
                            except:
                                loc = None
                            errors.append({"type": "Duplicate", "severity": "High",
                                "desc": f"Duplicate: feature {gdf.index[i]} and {gdf.index[j]}", "location": loc})
                    except: pass
            print(f"   Duplicates found: {dup_count}")

        # ============ 5. ATTRIBUTES (Null/Empty values) ============
        if checks.get('attributes', True):
            print("   Checking attributes...")
            attr_count = 0
            for col in gdf.columns:
                if col == 'geometry': continue
                for idx, val in gdf[col].items():
                    if val is None or (isinstance(val, str) and val.strip() == ''):
                        attr_count += 1
                        if attr_count <= 100:
                            try:
                                c = gdf_wgs84.geometry.iloc[idx].centroid
                                loc = {"lat": c.y, "lng": c.x}
                            except:
                                loc = None
                            errors.append({"type": "Attribute", "severity": "Medium",
                                "desc": f"Empty value in \"{col}\" for feature {idx}", "location": loc})
            if attr_count > 100:
                errors.append({"type": "Attribute", "severity": "Medium",
                    "desc": f"... and {attr_count - 100} more empty values"})
            print(f"   Attribute errors: {attr_count}")

        # ============ 6. SELF-INTERSECTION (PolygonToLine -> Intersect like ArcPy) ============
        if checks.get('self_intersect', False):
            ref_layer_name = checks.get('self_intersect_ref', '')
            
            if ref_layer_name:
                # === Mode 2: Line-Polygon Crossing (with reference layer) ===
                print(f"   Checking crossing with reference layer: {ref_layer_name}...")
                si_count = 0
                try:
                    ref_gdf = gpd.read_file(filepath, layer=ref_layer_name)
                    ref_wgs84 = ref_gdf.to_crs(epsg=4326) if ref_gdf.crs and str(ref_gdf.crs) != 'EPSG:4326' else ref_gdf
                    
                    # Determine which is line and which is polygon
                    main_types = gdf.geometry.geom_type.unique()
                    ref_types = ref_gdf.geometry.geom_type.unique()
                    
                    lines_gdf = None
                    polys_gdf = None
                    lines_wgs = None
                    polys_wgs = None
                    
                    if any(t in ['LineString', 'MultiLineString'] for t in main_types):
                        lines_gdf = gdf; lines_wgs = gdf_wgs84
                        polys_gdf = ref_gdf; polys_wgs = ref_wgs84
                    elif any(t in ['LineString', 'MultiLineString'] for t in ref_types):
                        lines_gdf = ref_gdf; lines_wgs = ref_wgs84
                        polys_gdf = gdf; polys_wgs = gdf_wgs84
                    else:
                        # Both polygons - check intersection between them
                        lines_gdf = gdf; lines_wgs = gdf_wgs84
                        polys_gdf = ref_gdf; polys_wgs = ref_wgs84
                    
                    if lines_gdf is not None and polys_gdf is not None:
                        for li, lrow in lines_gdf.iterrows():
                            for pi, prow in polys_gdf.iterrows():
                                try:
                                    if lrow.geometry.intersects(prow.geometry):
                                        inter = lrow.geometry.intersection(prow.geometry)
                                        if not inter.is_empty:
                                            si_count += 1
                                            try:
                                                # Transform intersection to WGS84
                                                if lines_gdf.crs and str(lines_gdf.crs) != 'EPSG:4326':
                                                    import pyproj
                                                    from shapely.ops import transform
                                                    project = pyproj.Transformer.from_crs(lines_gdf.crs, 'EPSG:4326', always_xy=True).transform
                                                    inter_wgs = transform(project, inter)
                                                else:
                                                    inter_wgs = inter
                                                c = inter_wgs.centroid
                                                loc = {"lat": c.y, "lng": c.x, "geojson": mapping(inter_wgs)}
                                            except:
                                                loc = None
                                            if si_count <= 50:
                                                errors.append({"type": "Self-Intersect", "severity": "High",
                                                    "desc": f"Crossing: line {li} × polygon {pi}", "location": loc})
                                except: pass
                except Exception as e:
                    print(f"   Crossing check error: {e}")
                print(f"   Crossings found: {si_count}")
            
            else:
                # === Mode 1: Self-Intersection (PolygonToLine method) ===
                print("   Checking self-intersections (PolygonToLine method)...")
                polys = gdf[gdf.geometry.geom_type.isin(['Polygon', 'MultiPolygon'])]
                si_count = 0
                
                if len(polys) > 0:
                    try:
                        from shapely.geometry import Point, LineString
                        
                        # Step 1: PolygonToLine
                        all_lines = []
                        line_to_poly = []
                        for idx, row in polys.iterrows():
                            geom = row.geometry
                            if geom.geom_type == 'MultiPolygon':
                                for poly in geom.geoms:
                                    all_lines.append(poly.boundary)
                                    line_to_poly.append(idx)
                            else:
                                all_lines.append(geom.boundary)
                                line_to_poly.append(idx)
                        
                        # Step 2: Check each boundary
                        affected_polys = set()
                        for li, line in enumerate(all_lines):
                            if line.is_empty: continue
                            if not line.is_simple:
                                affected_polys.add(line_to_poly[li])
                        
                        # Also check validity
                        for idx, row in polys.iterrows():
                            if idx in affected_polys: continue
                            if not row.geometry.is_valid:
                                affected_polys.add(idx)
                        
                        # Report errors
                        for poly_idx in affected_polys:
                            si_count += 1
                            try:
                                c = gdf_wgs84.loc[poly_idx].geometry.centroid
                                boundary_wgs = gdf_wgs84.loc[poly_idx].geometry.boundary
                                loc = {"lat": c.y, "lng": c.x, "geojson": mapping(boundary_wgs)}
                            except:
                                loc = None
                            if si_count <= 50:
                                errors.append({"type": "Self-Intersect", "severity": "High",
                                    "desc": f"Self-intersection (bowtie) at feature {poly_idx}", "location": loc})
                        
                    except Exception as e:
                        print(f"   Self-intersect error: {e}")
                
                print(f"   Self-intersections: {si_count}")

        # ============ 7. SPIKES (Angle detection - point on actual vertex) ============
        if checks.get('spikes', False):
            print("   Checking spikes...")
            angle_threshold = checks.get('spike_angle', 15.0)
            spike_count = 0
            
            # Prepare transformer for spike points
            _transformer = None
            if gdf.crs and str(gdf.crs) != 'EPSG:4326':
                try:
                    import pyproj
                    _transformer = pyproj.Transformer.from_crs(gdf.crs, 'EPSG:4326', always_xy=True)
                except: pass
            
            for idx, row in gdf.iterrows():
                try:
                    geom = row.geometry
                    parts = []
                    if geom.geom_type in ['Polygon', 'MultiPolygon']:
                        if geom.geom_type == 'MultiPolygon':
                            for p in geom.geoms: parts.append(list(p.exterior.coords))
                        else:
                            parts.append(list(geom.exterior.coords))
                    elif geom.geom_type in ['LineString', 'MultiLineString']:
                        if geom.geom_type == 'MultiLineString':
                            for l in geom.geoms: parts.append(list(l.coords))
                        else:
                            parts.append(list(geom.coords))
                    
                    for coords in parts:
                        for i in range(len(coords)):
                            if geom.geom_type in ['LineString', 'MultiLineString']:
                                if i == 0 or i == len(coords) - 1: continue
                            p1 = coords[i-1]
                            p2 = coords[i]
                            p3 = coords[(i+1) % len(coords)]
                            # Calculate angle
                            v1x, v1y = p1[0]-p2[0], p1[1]-p2[1]
                            v2x, v2y = p3[0]-p2[0], p3[1]-p2[1]
                            len1 = math.sqrt(v1x*v1x + v1y*v1y)
                            len2 = math.sqrt(v2x*v2x + v2y*v2y)
                            if len1 == 0 or len2 == 0: continue
                            dot = v1x*v2x + v1y*v2y
                            cos_a = max(-1.0, min(1.0, dot/(len1*len2)))
                            angle = math.degrees(math.acos(cos_a))
                            if angle < angle_threshold:
                                spike_count += 1
                                if spike_count <= 50:
                                    # Transform the actual spike vertex to WGS84
                                    try:
                                        if _transformer:
                                            lng, lat = _transformer.transform(p2[0], p2[1])
                                        else:
                                            lng, lat = p2[0], p2[1]
                                        loc = {"lat": lat, "lng": lng}
                                    except:
                                        loc = None
                                    errors.append({"type": "Spike", "severity": "Medium",
                                        "desc": f"Spike at feature {idx} vertex {i} (angle: {angle:.1f}°)",
                                        "location": loc})
                except: pass
            if spike_count > 50:
                errors.append({"type": "Spike", "severity": "Medium",
                    "desc": f"... and {spike_count - 50} more spikes"})
            print(f"   Spikes found: {spike_count}")

        # ============ 8. SPELLING / SIMILARITY CHECK ============
        if checks.get('spelling', False):
            print("   Checking spelling & similar values...")
            spell_count = 0
            
            from difflib import SequenceMatcher
            
            for col in gdf.columns:
                if col == 'geometry': continue
                
                # Get all non-empty string values (skip numbers)
                str_values = []
                for val in gdf[col]:
                    if val is not None:
                        s = str(val).strip()
                        if s and s.lower() != 'none':
                            # Skip if it's a number
                            try:
                                float(s)
                                continue
                            except ValueError:
                                str_values.append(s)
                
                unique_vals = list(set(str_values))
                print(f"      Field '{col}': {len(unique_vals)} unique values: {unique_vals[:10]}")
                
                if len(unique_vals) < 2: continue
                
                # Compare each pair for similarity
                for i in range(len(unique_vals)):
                    for j in range(i + 1, len(unique_vals)):
                        v1 = unique_vals[i]
                        v2 = unique_vals[j]
                        
                        if v1.lower() == v2.lower(): continue
                        
                        ratio = SequenceMatcher(None, v1.lower(), v2.lower()).ratio()
                        print(f"      Comparing '{v1}' vs '{v2}' = {ratio:.2f}")
                        
                        if ratio >= 0.6:
                            count1 = str_values.count(v1)
                            count2 = str_values.count(v2)
                            if count1 >= count2:
                                correct, wrong, wrong_count = v1, v2, count2
                            else:
                                correct, wrong, wrong_count = v2, v1, count1
                            
                            spell_count += 1
                            similarity_pct = round(ratio * 100)
                            if spell_count <= 100:
                                errors.append({"type": "Spelling", "severity": "Low",
                                    "desc": f"'{wrong}' -> '{correct}' ? ({similarity_pct}% similar) in field \"{col}\" ({wrong_count} features)"})
            
            if spell_count > 100:
                errors.append({"type": "Spelling", "severity": "Low",
                    "desc": f"... and {spell_count - 100} more similar values"})
            print(f"   Spelling/similarity issues: {spell_count}")

        # Summary
        summary = {}
        for e in errors:
            summary[e['type']] = summary.get(e['type'], 0) + 1
        print(f"   TOTAL ERRORS: {len(errors)}")


        return jsonify({'total': len(errors), 'errors': errors[:200], 'summary': summary})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/stats', methods=['POST'])
def layer_stats():
    data = request.json
    try:
        gdf = gpd.read_file(data['filepath'], layer=data['layer'])
        return jsonify(get_layer_stats(gdf))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/convert', methods=['POST'])
def convert_layer():
    data = request.json
    filepath, layer_name = data['filepath'], data['layer']
    fmt = data.get('format', 'geojson')

    try:
        gdf = gpd.read_file(filepath, layer=layer_name)
        if gdf.crs and str(gdf.crs) != 'EPSG:4326':
            gdf = gdf.to_crs('EPSG:4326')

        out_dir = tempfile.mkdtemp()
        drivers = {'geojson': 'GeoJSON', 'shp': 'ESRI Shapefile', 'gpkg': 'GPKG', 'kml': 'KML'}

        if fmt == 'csv':
            out = os.path.join(out_dir, f'{layer_name}.csv')
            df = gdf.copy()
            df['longitude'] = gdf.geometry.centroid.x
            df['latitude'] = gdf.geometry.centroid.y
            df.drop(columns=['geometry']).to_csv(out, index=False, encoding='utf-8-sig')
        else:
            ext = fmt if fmt != 'shp' else 'shp'
            out = os.path.join(out_dir, f'{layer_name}.{ext}')
            gdf.to_file(out, driver=drivers[fmt])

        if fmt == 'shp':
            zp = os.path.join(out_dir, f'{layer_name}.zip')
            with zipfile.ZipFile(zp, 'w') as z:
                for f in os.listdir(out_dir):
                    if not f.endswith('.zip'):
                        z.write(os.path.join(out_dir, f), f)
            out = zp

        return send_from_directory(os.path.dirname(out), os.path.basename(out), as_attachment=True)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== HELPERS ====================

def find_gis_file(directory):
    """ابحث عن أي ملف GIS داخل مجلد"""
    # 1. .gdb folder
    for root, dirs, files in os.walk(directory):
        for d in dirs:
            if d.lower().endswith('.gdb'):
                return os.path.join(root, d)
    # 2. .gdbtable files (gdb بدون مجلد)
    for root, dirs, files in os.walk(directory):
        if any(f.endswith('.gdbtable') for f in files):
            return root
    # 3. Other formats
    for ext in ['.shp', '.gpkg', '.geojson', '.json', '.kml']:
        for root, dirs, files in os.walk(directory):
            for f in files:
                if f.lower().endswith(ext):
                    return os.path.join(root, f)
    return None


def read_gis_file(filepath):
    filepath = os.path.normpath(filepath)
    layers_info = []

    try:
        available_layers = fiona.listlayers(filepath)
    except:
        available_layers = [Path(filepath).stem]

    for name in available_layers:
        try:
            gdf = gpd.read_file(filepath, layer=name)
            layers_info.append({
                'name': name,
                'feature_count': len(gdf),
                'geometry_type': gdf.geometry.geom_type.unique().tolist() if len(gdf) > 0 else ['Unknown'],
                'fields': [c for c in gdf.columns if c != 'geometry'],
                'crs': str(gdf.crs) if gdf.crs else 'Unknown',
                'bounds': gdf.total_bounds.tolist() if len(gdf) > 0 else None,
            })
        except Exception as e:
            layers_info.append({'name': name, 'error': str(e), 'feature_count': 0,
                'geometry_type': ['Error'], 'fields': []})

    return {'filepath': filepath, 'total_layers': len(layers_info), 'layers': layers_info}


def get_layer_stats(gdf):
    stats = {
        'feature_count': len(gdf),
        'geometry_type': gdf.geometry.geom_type.unique().tolist() if len(gdf) > 0 else [],
        'crs': str(gdf.crs) if gdf.crs else 'Unknown',
        'fields': [], 'geometry_stats': {}
    }

    for col in gdf.columns:
        if col == 'geometry': continue
        info = {'name': col, 'dtype': str(gdf[col].dtype),
            'null_count': int(gdf[col].isnull().sum()),
            'unique_count': int(gdf[col].nunique())}
        if gdf[col].dtype in ['int64','float64','int32','float32']:
            if not gdf[col].isnull().all():
                info.update({'min': float(gdf[col].min()), 'max': float(gdf[col].max()),
                    'mean': round(float(gdf[col].mean()), 2), 'sum': round(float(gdf[col].sum()), 2)})
        elif gdf[col].dtype == 'object':
            info['top_values'] = {str(k): int(v) for k, v in gdf[col].value_counts().head(10).to_dict().items()}
        stats['fields'].append(info)

    if len(gdf) > 0:
        gt = gdf.geometry.geom_type.iloc[0]
        try:
            gdf_p = gdf.to_crs('EPSG:32637')
            if gt in ['Polygon', 'MultiPolygon']:
                a, p = gdf_p.geometry.area, gdf_p.geometry.length
                stats['geometry_stats'] = {
                    'total_area_sqm': round(float(a.sum()),2), 'avg_area_sqm': round(float(a.mean()),2),
                    'min_area_sqm': round(float(a.min()),2), 'max_area_sqm': round(float(a.max()),2),
                    'total_perimeter_m': round(float(p.sum()),2), 'avg_perimeter_m': round(float(p.mean()),2)}
            elif gt in ['LineString', 'MultiLineString']:
                l = gdf_p.geometry.length
                stats['geometry_stats'] = {
                    'total_length_m': round(float(l.sum()),2), 'avg_length_m': round(float(l.mean()),2),
                    'min_length_m': round(float(l.min()),2), 'max_length_m': round(float(l.max()),2)}
        except: pass

    return stats


@app.route('/api/report-pdf', methods=['POST'])
def generate_report():
    """تصدير تقرير فحص الأخطاء كـ PDF"""
    data = request.json
    filepath = data.get('filepath')
    layer_name = data.get('layer')
    errors_data = data.get('errors', [])
    summary = data.get('summary', {})
    total = data.get('total', 0)

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.units import cm, mm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.lib.enums import TA_RIGHT, TA_CENTER
        import datetime

        # Try to register a good font (Windows first, then Linux)
        FONT = 'Helvetica'
        font_paths = [
            'C:/Windows/Fonts/arial.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        ]
        for fp in font_paths:
            try:
                pdfmetrics.registerFont(TTFont('CustomFont', fp))
                FONT = 'CustomFont'
                break
            except: continue

        output_dir = tempfile.mkdtemp()
        pdf_path = os.path.join(output_dir, f'report_{layer_name}.pdf')

        doc = SimpleDocTemplate(pdf_path, pagesize=A4, 
            rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)

        styles = {
            'title': ParagraphStyle('title', fontName=FONT, fontSize=22, 
                alignment=TA_CENTER, spaceAfter=10, textColor=colors.HexColor('#0a0e1a')),
            'subtitle': ParagraphStyle('subtitle', fontName=FONT, fontSize=12, 
                alignment=TA_CENTER, spaceAfter=20, textColor=colors.HexColor('#666666')),
            'heading': ParagraphStyle('heading', fontName=FONT, fontSize=14, 
                spaceAfter=10, spaceBefore=15, textColor=colors.HexColor('#00c78a')),
            'body': ParagraphStyle('body', fontName=FONT, fontSize=10, 
                alignment=TA_RIGHT, spaceAfter=5),
            'small': ParagraphStyle('small', fontName=FONT, fontSize=8, 
                alignment=TA_CENTER, textColor=colors.HexColor('#999999')),
        }

        elements = []

        # Header
        elements.append(Paragraph("GeoTools Pro", styles['title']))
        elements.append(Paragraph(f"Error Check Report — {layer_name}", styles['subtitle']))
        elements.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor('#00e5a0')))
        elements.append(Spacer(1, 15))

        # Info
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        info_data = [
            ['Date', now],
            ['Layer', layer_name],
            ['File', os.path.basename(filepath) if filepath else 'N/A'],
            ['Total Errors', str(total)],
        ]
        info_table = Table(info_data, colWidths=[4*cm, 12*cm])
        info_table.setStyle(TableStyle([
            ('FONTNAME', (0,0), (-1,-1), FONT),
            ('FONTSIZE', (0,0), (-1,-1), 10),
            ('TEXTCOLOR', (0,0), (0,-1), colors.HexColor('#00c78a')),
            ('FONTNAME', (0,0), (0,-1), FONT),
            ('ALIGN', (0,0), (-1,-1), 'LEFT'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ]))
        elements.append(info_table)
        elements.append(Spacer(1, 15))

        # Summary table
        elements.append(Paragraph("Error Summary", styles['heading']))
        
        sum_header = ['Error Type', 'Count', 'Status']
        sum_rows = [sum_header]
        for err_type in ['Overlap', 'Gap', 'Dangle', 'Duplicate', 'Attribute']:
            count = summary.get(err_type, 0)
            status = 'PASS' if count == 0 else 'FAIL'
            sum_rows.append([err_type, str(count), status])
        sum_rows.append(['TOTAL', str(total), 'PASS' if total == 0 else 'FAIL'])

        sum_table = Table(sum_rows, colWidths=[6*cm, 4*cm, 6*cm])
        sum_table.setStyle(TableStyle([
            ('FONTNAME', (0,0), (-1,-1), FONT),
            ('FONTSIZE', (0,0), (-1,-1), 10),
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0a0e1a')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#dddddd')),
            ('ROWBACKGROUNDS', (0,1), (-1,-2), [colors.white, colors.HexColor('#f8f8f8')]),
            ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor('#f0f0f0')),
            ('FONTNAME', (0,-1), (-1,-1), FONT),
            ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ('TOPPADDING', (0,0), (-1,-1), 8),
        ]))

        # Color status cells
        for i, row in enumerate(sum_rows[1:], 1):
            if row[2] == 'PASS':
                sum_table.setStyle(TableStyle([('TEXTCOLOR', (2,i), (2,i), colors.HexColor('#00c78a'))]))
            else:
                sum_table.setStyle(TableStyle([('TEXTCOLOR', (2,i), (2,i), colors.HexColor('#ef4444'))]))

        elements.append(sum_table)
        elements.append(Spacer(1, 20))

        # Error details
        if errors_data and len(errors_data) > 0:
            elements.append(Paragraph("Error Details", styles['heading']))
            
            detail_header = ['#', 'Type', 'Severity', 'Description']
            detail_rows = [detail_header]
            for i, err in enumerate(errors_data[:100], 1):
                detail_rows.append([
                    str(i),
                    err.get('type', ''),
                    err.get('severity', ''),
                    err.get('desc', '')[:60]
                ])

            detail_table = Table(detail_rows, colWidths=[1.5*cm, 3*cm, 3*cm, 8.5*cm])
            detail_table.setStyle(TableStyle([
                ('FONTNAME', (0,0), (-1,-1), FONT),
                ('FONTSIZE', (0,0), (-1,-1), 8),
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0a0e1a')),
                ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                ('ALIGN', (0,0), (2,-1), 'CENTER'),
                ('ALIGN', (3,0), (3,-1), 'LEFT'),
                ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#dddddd')),
                ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#fafafa')]),
                ('BOTTOMPADDING', (0,0), (-1,-1), 5),
                ('TOPPADDING', (0,0), (-1,-1), 5),
            ]))
            elements.append(detail_table)

        # Footer
        elements.append(Spacer(1, 30))
        elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#eeeeee')))
        elements.append(Spacer(1, 5))
        elements.append(Paragraph(f"Generated by GeoTools Pro — {now}", styles['small']))

        doc.build(elements)

        return send_from_directory(os.path.dirname(pdf_path), os.path.basename(pdf_path), as_attachment=True)

    except ImportError:
        return jsonify({'error': 'مكتبة reportlab مو مثبتة. شغّل: pip install reportlab'}), 500
    except Exception as e:
        print(f"❌ PDF Error: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/convert-cad', methods=['POST', 'OPTIONS'])
def convert_cad():
    """تحويل ملف CAD (DWG/DXF) إلى GIS"""
    if request.method == 'OPTIONS':
        return '', 204

    print("\n📐 === CAD Conversion ===")

    try:
        if 'file' not in request.files:
            return jsonify({'error': 'لم يتم رفع ملف'}), 400

        file = request.files['file']
        if not file or file.filename == '':
            return jsonify({'error': 'اختر ملف CAD'}), 400

        original_name = file.filename
        print(f"📄 CAD file: {original_name}")

        upload_id = str(uuid.uuid4())[:8]
        upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], f'cad_{upload_id}')
        os.makedirs(upload_dir, exist_ok=True)

        safe_name = secure_filename(original_name)
        if not safe_name:
            ext = Path(original_name).suffix.lower() or '.dxf'
            safe_name = f"cad_{upload_id}{ext}"

        filepath = os.path.join(upload_dir, safe_name)
        file.save(filepath)

        print(f"💾 Saved: {filepath}")

        # Read CAD file with fiona/geopandas
        layers_info = []
        
        try:
            available_layers = fiona.listlayers(filepath)
            print(f"📑 CAD layers: {available_layers}")
        except:
            available_layers = [Path(filepath).stem]

        all_gdfs = {}
        for layer_name in available_layers:
            try:
                gdf = gpd.read_file(filepath, layer=layer_name)
                if len(gdf) > 0:
                    # Clean up CAD data
                    # Remove empty geometries
                    gdf = gdf[~gdf.geometry.is_empty]
                    gdf = gdf[gdf.geometry.notna()]
                    
                    # Separate by geometry type
                    for geom_type in gdf.geometry.geom_type.unique():
                        subset = gdf[gdf.geometry.geom_type == geom_type]
                        if len(subset) > 0:
                            key = f"{layer_name}_{geom_type}"
                            all_gdfs[key] = subset
                            layers_info.append({
                                'name': key,
                                'original_layer': layer_name,
                                'feature_count': len(subset),
                                'geometry_type': [geom_type],
                                'fields': [c for c in subset.columns if c != 'geometry'],
                                'crs': str(subset.crs) if subset.crs else 'Unknown',
                            })
                            print(f"   ✅ {key}: {len(subset)} features")
            except Exception as e:
                print(f"   ⚠️ {layer_name}: {e}")

        if not layers_info:
            return jsonify({'error': 'لم يتم العثور على بيانات في ملف CAD'}), 400

        # Save as GeoPackage (all layers in one file)
        output_path = os.path.join(upload_dir, f'{Path(safe_name).stem}_converted.gpkg')
        for key, gdf in all_gdfs.items():
            gdf.to_file(output_path, layer=key, driver='GPKG')
        
        print(f"✅ Saved GeoPackage: {output_path}")

        return jsonify({
            'filepath': output_path,
            'original_file': original_name,
            'total_layers': len(layers_info),
            'layers': layers_info,
            'output_file': output_path,
            'message': f'تم تحويل {len(layers_info)} طبقة بنجاح'
        })

    except Exception as e:
        print(f"❌ CAD Error: {e}\n{traceback.format_exc()}")
        return jsonify({'error': f'خطأ في تحويل CAD: {e}'}), 500


if __name__ == '__main__':
    print(f"""
╔══════════════════════════════════════════╗
║        🌍 GeoTools Pro                   ║
╠══════════════════════════════════════════╣
║  🔗 http://localhost:5000                ║
║  📂 Upload: .gdb(ZIP) .shp .gpkg .geojson║
║  📐 CAD: .dxf .dwg                       ║
║  ⏹️  Ctrl+C to stop                      ║
╚══════════════════════════════════════════╝
""")
    app.run(debug=True, host='0.0.0.0', port=5000)
