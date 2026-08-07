"""
Main Panel module for the GEE Data Extractor.
Contains the primary extraction pipeline UI with 4 sections:
1. Data Source (WHAT) - Satellite and variable selection
2. Region of Interest (WHERE) - Point/Shapefile/GADM selection
3. Time Definition (WHEN) - Year and DOY selection
4. Execution (HOW) - Run extraction
"""
import streamlit as st
import ee
import json
from pathlib import Path
import os
import threading
from datetime import datetime

from src.infrastructure.configuration.SettingsService import SettingsService
from src.application.services.GeometryService import GeometryService
from src.infrastructure.persistence.HistoryManager import HistoryManager
from src.interface.map_utils import (
    create_base_map, add_geojson_overlay, add_markers, render_map,
    render_map_display, gdf_to_geojson, extract_gadm_display_columns,
    fetch_gadm_boundaries,
)
from src.infrastructure.utils.maps_url_parser import (
    parse_google_maps_url, is_google_maps_url,
)


def load_satellites():
    """Load satellite configurations from JSON."""
    # Try config folder first, then root
    config_path = Path("config/satellites.json")
    if not config_path.exists():
        config_path = Path("satellites.json")
    
    if config_path.exists():
        with open(config_path, 'r') as f:
            data = json.load(f)
            return data.get('satellites', [])
    return []


def update_default_filename():
    """Update the custom filename in session state based on current selections."""
    # Get current satellite ID
    # Note: satellite_selector key in session state holds the name
    sat_name = st.session_state.get('satellite_selector')
    satellites = load_satellites()
    selected_satellite = next((s for s in satellites if s['name'] == sat_name), None)
    sat_id = selected_satellite['id'] if selected_satellite else "GEE"
    
    # Get current years (prefer form values if in middle of submission, else session state)
    start = st.session_state.get('form_start_year') or st.session_state.get('start_year', 2020)
    end = st.session_state.get('form_end_year') or st.session_state.get('end_year', datetime.now().year)
    
    st.session_state.custom_filename = f"{sat_id}_{start}_{end}_timeseries"




def snapshot_config_from_session() -> dict:
    """Build a portable extraction config from the current form session state."""
    satellites = load_satellites()
    sat_name = st.session_state.get("satellite_selector")
    selected_satellite = next((s for s in satellites if s["name"] == sat_name), None)
    sat_id = selected_satellite["id"] if selected_satellite else None

    roi_method = st.session_state.get("roi_method", "")
    if "Point" in str(roi_method):
        geometry_source = "Points"
    elif "File" in str(roi_method):
        geometry_source = "Shapefile"
    elif "GADM" in str(roi_method):
        geometry_source = "GADM"
    else:
        geometry_source = "Points"

    export_method_ui = st.session_state.get("export_method", "")
    if "Drive" in str(export_method_ui):
        export_method = "Drive"
    elif "Local" in str(export_method_ui) or "Download" in str(export_method_ui):
        export_method = "Local"
    else:
        export_method = "Drive"

    gadm_selection = st.session_state.get("gadm_selection") or {}
    if isinstance(gadm_selection, dict):
        gadm_selection = {k: v for k, v in gadm_selection.items() if k != "gdf"}
    else:
        gadm_selection = {}

    dates = st.session_state.get("date_config") or {
        "start_year": st.session_state.get("form_start_year") or st.session_state.get("start_year"),
        "end_year": st.session_state.get("form_end_year") or st.session_state.get("end_year"),
        "start_doy": st.session_state.get("form_start_doy") or st.session_state.get("start_doy", 1),
        "end_doy": st.session_state.get("form_end_doy") or st.session_state.get("end_doy", 365),
        "use_season": st.session_state.get("use_season_interactive", st.session_state.get("use_season", False)),
    }

    selected_points = st.session_state.get("selected_points") or []
    uploaded = st.session_state.get("uploaded_shapefile")

    return {
        "satellite": sat_id,
        "bands": st.session_state.get("selected_bands") or st.session_state.get("band_multiselect") or [],
        "reducers": dict(st.session_state.get("band_selections") or {}),
        "geometry_source": geometry_source,
        "num_points": len(selected_points),
        "selected_points": selected_points,
        "gadm_selection": gadm_selection,
        "gadm_regions": st.session_state.get("gadm_regions"),
        "uploaded_shapefile": uploaded,
        "dates": dates,
        "export_method": export_method,
        "output_format": "CSV",
        "custom_filename": st.session_state.get("custom_filename"),
    }


def apply_loaded_settings():
    """Applies settings from history or a preset to session state."""
    loaded = st.session_state.get('loaded_settings')
    if not loaded:
        return

    satellites = load_satellites()

    # 1. Restore Satellite — unknown id blocks apply (presets / stale catalogs)
    sat_id = loaded.get('satellite')
    sat_name = None
    selected_sat = None
    if sat_id:
        selected_sat = next((s for s in satellites if s['id'] == sat_id), None)
        if selected_sat:
            sat_name = selected_sat['name']
            st.session_state.satellite_selector = sat_name
        else:
            st.error(
                f'We don\'t recognize satellite "{sat_id}" in this app version. '
                "Update the catalog or pick another dataset."
            )
            st.session_state.loaded_settings = {}
            return

    # 2. Restore Bands (skip names missing from the current catalog)
    bands_to_apply = list(loaded.get('bands') or [])
    if selected_sat and bands_to_apply:
        available = {b['name'] for b in selected_sat.get('bands', [])}
        missing = [b for b in bands_to_apply if b not in available]
        bands_to_apply = [b for b in bands_to_apply if b in available]
        if missing:
            st.warning(
                "Some bands are not in this catalog and were skipped: "
                + ", ".join(missing)
            )
    if bands_to_apply:
        st.session_state.band_multiselect = bands_to_apply

    # 3. Restore Reducers (Band Selections)
    if loaded.get('reducers'):
        reducers = dict(loaded['reducers'])
        if bands_to_apply:
            reducers = {k: v for k, v in reducers.items() if k in bands_to_apply}
        st.session_state.band_selections = reducers
        for band, reducer in reducers.items():
            st.session_state[f"reducer_{band}"] = reducer

    # 4. Restore ROI (Points, Shapefile, GADM)
    geo_source = loaded.get('geometry_source')
    shapefile_required = bool(loaded.get('shapefile_required')) or geo_source == 'Shapefile'
    if geo_source == 'Points':
        st.session_state.roi_method = "📍 Point Coordinates"
    elif geo_source == 'Shapefile' or shapefile_required:
        st.session_state.roi_method = "📁 File Import"
    elif geo_source == 'GADM':
        st.session_state.roi_method = "🗺️ GADM Admin"

    if loaded.get('selected_points') is not None:
        st.session_state.selected_points = loaded.get('selected_points') or []

    shapefile_path = loaded.get('uploaded_shapefile')
    if shapefile_required and not shapefile_path:
        st.session_state.uploaded_shapefile = None
        st.session_state.import_file_path = ""
        st.warning(
            "This preset uses a shapefile. Paths aren't shared, so please choose your local file under File Import."
        )
    elif shapefile_path:
        st.session_state.import_file_path = shapefile_path
        if os.path.exists(shapefile_path):
            st.session_state.uploaded_shapefile = shapefile_path
            st.toast(f"✅ Restored shapefile: {os.path.basename(shapefile_path)}")
        else:
            st.session_state.uploaded_shapefile = None
            st.warning(f"⚠️ File path restored but not found on disk: {shapefile_path}")

    if loaded.get('gadm_selection'):
        st.session_state.gadm_selection = loaded['gadm_selection']
        gadm = loaded['gadm_selection']

        st.session_state.gadm_country = gadm.get('name', '')
        st.session_state.gadm_level = gadm.get('admin_level', 0)

        st.session_state.gadm_country_loaded = None
        st.session_state.gadm_level_loaded = None
        st.session_state.gadm_gdf = None
        st.session_state.pop('gadm_map_visible', None)

        if loaded.get('gadm_regions'):
            st.session_state.gadm_regions = loaded['gadm_regions']

    # 5. Restore Time/Dates
    if loaded.get('dates'):
        dates = loaded['dates']
        st.session_state.date_config = dates
        st.session_state.form_start_year = dates.get('start_year')
        st.session_state.form_end_year = dates.get('end_year')
        st.session_state.form_start_doy = dates.get('start_doy', 1)
        st.session_state.form_end_doy = dates.get('end_doy', 365)
        st.session_state.use_season_interactive = dates.get('use_season', False)
        st.session_state.start_year = dates.get('start_year')
        st.session_state.end_year = dates.get('end_year')
        st.session_state.start_doy = dates.get('start_doy', 1)
        st.session_state.end_doy = dates.get('end_doy', 365)
        st.session_state.use_season = dates.get('use_season', False)

    # 6. Restore Execution Settings
    if loaded.get('export_method'):
        saved_method = loaded['export_method']
        if 'Drive' in saved_method:
            st.session_state.export_method = "☁️ Save to Google Drive (Batch)"
        elif 'Local' in saved_method or 'Download' in saved_method:
            st.session_state.export_method = "💾 Download Locally (Interactive)"

    if loaded.get('custom_filename'):
        st.session_state.custom_filename = loaded['custom_filename']

    st.session_state.loaded_settings = {}
    st.toast("✅ Settings restored successfully!")



def render(settings_service: SettingsService):
    """Renders the main panel with extraction pipeline."""
    st.title("🛰️ GEE Data Extractor")
    
    # Apply loaded settings if any
    apply_loaded_settings()
    
    # Load satellites configuration
    satellites = load_satellites()
    
    # Check for loaded settings from history
    # We still use this dict for initial values in widgets before they are keyed to session state
    # But apply_loaded_settings handles the complex state
    loaded_settings = {} # Cleared by apply_loaded_settings, but we might want to keep a copy? 
    # Actually, apply_loaded_settings applies to SESSION STATE.
    # The widgets below read from loaded_settings OR session state.
    # To avoid double-handling, we should rely on session_state where possible.
    # However, some widgets (like satellite selector) trigger reruns on change.
    # Let's keep loaded_settings as empty since we applied it.

    
    # Initialize session state for points if not exists
    if 'selected_points' not in st.session_state:
        st.session_state.selected_points = []
    
    # Section 1: Data Source (WHAT)
    render_data_source_section(satellites, loaded_settings)
    
    st.divider()
    
    # Section 2: Region of Interest (WHERE)
    render_roi_section(loaded_settings)
    
    st.divider()
    
    # Section 3: Time Definition (WHEN)
    render_time_section(loaded_settings)
    
    st.divider()
    
    # Section 4: Execution (HOW)
    render_execution_section(settings_service, satellites)


def render_data_source_section(satellites: list, loaded_settings: dict):
    """Section 1: Data Source - Satellite and variable selection."""
    st.header("1️⃣ Data Source")
    
    if not satellites:
        st.error("No satellite configurations found. Please check satellites.json")
        return
    
    # Build satellite options
    satellite_options = {sat['name']: sat for sat in satellites}
    satellite_names = list(satellite_options.keys())
    
    # Default selection from loaded settings or settings
    default_idx = 0
    if loaded_settings.get('satellite'):
        for i, name in enumerate(satellite_names):
            if satellite_options[name]['id'] == loaded_settings.get('satellite'):
                default_idx = i
                break
    
    # Satellite selector
    selected_sat_name = st.selectbox(
        "Select Satellite/Dataset",
        options=satellite_names,
        index=default_idx,
        key="satellite_selector",
        on_change=update_default_filename
    )
    
    selected_satellite = satellite_options[selected_sat_name]
    
    # Display satellite info in expander
    with st.expander("ℹ️ Dataset Information", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"**ID:** `{selected_satellite['id']}`")
            st.markdown(f"**GEE Collection:** `{selected_satellite['ee_collection_name']}`")
            st.markdown(f"**Start Date:** {selected_satellite.get('startDate', 'N/A')}")
        with col2:
            st.markdown(f"**Pixel Size:** {selected_satellite.get('pixelSize', 'N/A')}m")
            if selected_satellite.get('website'):
                st.markdown(f"[📖 Documentation]({selected_satellite['website']})")
        st.markdown(f"**Description:** {selected_satellite.get('description', 'No description available')}")
    
    # Variable/Band selection
    bands = selected_satellite.get('bands', [])
    if bands:
        st.subheader("Select Variables/Bands")
        
        # Store band selections with reducers
        if 'band_selections' not in st.session_state:
            st.session_state.band_selections = {}
        
        band_names = [b['name'] for b in bands]
        
        # Default bands from loaded settings
        default_bands = loaded_settings.get('bands', [])
        default_selections = [b for b in band_names if b in default_bands] or band_names[:1]
        
        selected_bands = st.multiselect(
            "Select bands to extract",
            options=band_names,
            default=default_selections if any(b in band_names for b in default_selections) else [],
            key="band_multiselect"
        )
        
        # For each selected band, show reducer option
        if selected_bands:
            st.markdown(
                "**Reducer per Variable:** ", help="When extracting data over a shape or buffer, multiple pixels may fall within it. "
                "The reducer defines how those pixel values are aggregated — e.g. &quot;mean&quot; averages all "
                "pixel values within the shape, &quot;max&quot; takes the highest value, etc."
            )
            reducers = ['mean', 'sum', 'max', 'min', 'median', 'first']
            
            for band_name in selected_bands:
                col1, col2 = st.columns([2, 1])
                with col1:
                    # Find band info
                    band_info = next((b for b in bands if b['name'] == band_name), {})
                    units = band_info.get('units', '')
                    desc = band_info.get('description', '')
                    st.caption(f"**{band_name}** ({units}) - {desc[:50]}...")
                with col2:
                    reducer = st.selectbox(
                        f"Reducer",
                        options=reducers,
                        index=0,
                        key=f"reducer_{band_name}",
                        label_visibility="collapsed"
                    )
                    st.session_state.band_selections[band_name] = reducer
        
        st.session_state.selected_satellite = selected_satellite
        st.session_state.selected_bands = selected_bands
    else:
        st.warning("No bands available for this dataset")


def render_roi_section(loaded_settings: dict):
    """Section 2: Region of Interest - Geometry selection."""
    st.header("2️⃣ Region of Interest")
    
    # Input method selector
    roi_method = st.radio(
        "Select input method",
        options=["📍 Point Coordinates", "📁 File Import", "🗺️ GADM Admin"],
        horizontal=True,
        key="roi_method"
    )
    
    geometry_service = GeometryService()
    
    if "📍 Point" in roi_method:
        render_point_input(loaded_settings)
    elif "📁 File" in roi_method:
        render_shapefile_input()
    elif "🗺️ GADM" in roi_method:
        render_gadm_input()
    
    # Display map for verification
    render_verification_map()


def render_point_input(loaded_settings: dict):
    """Point coordinate input with multiple points support."""
    st.subheader("Point Selection")
    
    # Manual entry
    st.markdown("**Add point manually:**")
    col1, col2, col3 = st.columns([2, 2, 1])
    with col1:
        lat = st.number_input("Latitude", value=0.0, min_value=-90.0, max_value=90.0, step=0.01, key="lat_input")
    with col2:
        lon = st.number_input("Longitude", value=0.0, min_value=-180.0, max_value=180.0, step=0.01, key="lon_input")
    with col3:
        st.write("")  # Spacer
        st.write("")
        if st.button("➕ Add", key="add_point_btn"):
            if lat != 0.0 or lon != 0.0:
                point = {'lat': lat, 'lon': lon}
                if point not in st.session_state.selected_points:
                    st.session_state.selected_points.append(point)
                    st.success(f"Added point ({lat}, {lon})")
                    st.rerun()

    # Google Maps Link
    with st.expander("🌐 Add from Google Maps Link"):
        st.caption(
            "Paste a Google Maps link to extract its coordinates. "
            "Works with both full URLs and short share links."
        )
        gmaps_url = st.text_input(
            "Google Maps URL",
            key="gmaps_url_input",
            placeholder="https://maps.app.goo.gl/... or https://www.google.com/maps/...",
            label_visibility="collapsed",
        )
        if st.button("🔗 Add Point", key="add_gmaps_point_btn", use_container_width=True):
            if not gmaps_url or not gmaps_url.strip():
                st.warning("Please paste a Google Maps link first.")
            elif not is_google_maps_url(gmaps_url):
                st.error("❌ This doesn't appear to be a Google Maps link.")
            else:
                with st.spinner("Resolving link..."):
                    coords = parse_google_maps_url(gmaps_url)
                if coords:
                    lat, lon = round(coords[0], 6), round(coords[1], 6)
                    point = {'lat': lat, 'lon': lon}
                    if point not in st.session_state.selected_points:
                        st.session_state.selected_points.append(point)
                        st.success(f"✅ Added point ({lat}, {lon})")
                        st.rerun()
                    else:
                        st.info("This point already exists in your selection.")
                else:
                    st.error(
                        "❌ Could not extract coordinates from this link. "
                        "Try copying the full URL from your browser's address bar."
                    )
        st.caption(
            "💡 **Tip:** Right-click a location on Google Maps → **Share** → copy the link. "
            "Or simply copy the URL from your browser."
        )

    # CSV Upload
    with st.expander("📂 Upload points from CSV"):
        csv_file = st.file_uploader("Upload CSV file", type=['csv'], key="point_csv_uploader", help="CSV must contain 'lat' and 'lon' columns (or 'latitude'/'longitude')")
        if csv_file:
            try:
                import pandas as pd
                df = pd.read_csv(csv_file)
                
                # Detect columns
                lat_cols = [c for c in df.columns if c.lower() in ['lat', 'latitude', 'y', 'lat_dec']]
                lon_cols = [c for c in df.columns if c.lower() in ['lon', 'longitude', 'x', 'lon_dec', 'lng']]
                
                if not lat_cols or not lon_cols:
                    st.error("❌ Could not find latitude/longitude columns. Please ensure they are named 'lat' and 'lon'.")
                else:
                    lat_col = lat_cols[0]
                    lon_col = lon_cols[0]
                    
                    new_points = []
                    for _, row in df.iterrows():
                        p = {'lat': round(float(row[lat_col]), 6), 'lon': round(float(row[lon_col]), 6)}
                        if p not in st.session_state.selected_points and p not in new_points:
                            new_points.append(p)
                    
                    if new_points:
                        st.session_state.selected_points.extend(new_points)
                        st.success(f"✅ Added {len(new_points)} points from CSV!")
                        st.rerun()
                    else:
                        st.info("No new unique points found in CSV.")
            except Exception as e:
                st.error(f"Error reading CSV: {e}")

    
    # Interactive map for clicking
    st.markdown("**Or click on map to add points:**")
    
    # Create folium map
    center = [0, 0]
    if st.session_state.selected_points:
        center = [st.session_state.selected_points[-1]['lat'], 
                  st.session_state.selected_points[-1]['lon']]
    
    m = create_base_map(center=center, zoom=3)

    # Add existing points to map
    add_markers(m, st.session_state.selected_points, color='red')

    map_data = render_map(m, key="point_map")
    
    # Handle map click
    if map_data and map_data.get('last_clicked'):
        clicked = map_data['last_clicked']
        new_point = {'lat': round(clicked['lat'], 6), 'lon': round(clicked['lng'], 6)}
        if new_point not in st.session_state.selected_points:
            st.session_state.selected_points.append(new_point)
            st.rerun()
    
    # Display selected points with delete option
    if st.session_state.selected_points:
        st.markdown("**Selected Points:**")
        for i, pt in enumerate(st.session_state.selected_points):
            col1, col2 = st.columns([4, 1])
            with col1:
                st.text(f"Point {i+1}: ({pt['lat']}, {pt['lon']})")
            with col2:
                if st.button("🗑️", key=f"del_pt_{i}"):
                    st.session_state.selected_points.pop(i)
                    st.rerun()
        
        if st.button("Clear All Points"):
            st.session_state.selected_points = []
            st.rerun()


def _open_file_dialog():
    """Open a native file dialog in a separate thread (tkinter requires this from Streamlit)."""
    import tkinter as tk
    from tkinter import filedialog
    
    result = [None]
    
    def run_dialog():
        root = tk.Tk()
        root.withdraw()  # Hide the main window
        root.attributes('-topmost', True)  # Bring dialog to front
        file_path = filedialog.askopenfilename(
            title="Select a geometry file",
            filetypes=[
                ("All supported", "*.shp *.geojson *.json *.kml *.zip"),
                ("Shapefile", "*.shp"),
                ("GeoJSON", "*.geojson *.json"),
                ("KML", "*.kml"),
                ("Zipped Shapefile", "*.zip"),
                ("All files", "*.*")
            ]
        )
        root.destroy()
        result[0] = file_path
    
    # Run in a thread to avoid Streamlit/tkinter event loop conflicts
    thread = threading.Thread(target=run_dialog)
    thread.start()
    thread.join()
    return result[0]


def render_shapefile_input():
    """File import: Shapefile, GeoJSON, KML with path selector and button-triggered map preview."""
    st.subheader("File Import")
    
    geometry_service = GeometryService()
    
    # --- Apply pending state changes BEFORE widget creation ---
    # (Streamlit allows setting widget keys before the widget is instantiated)
    if st.session_state.get('_pending_file_path') is not None:
        st.session_state.import_file_path = st.session_state._pending_file_path
        del st.session_state._pending_file_path
    
    # --- File path input with Browse button ---
    st.markdown("**Select a geometry file** (`.shp`, `.geojson`, `.kml`, `.zip`):")
    col1, col2 = st.columns([4, 1], vertical_alignment="bottom")
    with col1:
        file_path = st.text_input(
            "File path",
            key="import_file_path",
            label_visibility="collapsed",
            placeholder="C:\\path\\to\\your\\file.shp"
        )
    with col2:
        if st.button("📂 Browse", key="browse_file_btn", use_container_width=True):
            selected = _open_file_dialog()
            if selected:
                # Queue the path for next rerun (can't set widget key after instantiation)
                st.session_state._pending_file_path = selected
                st.session_state.imported_geodata = None
                st.session_state.import_preview_ready = False
                st.rerun()
    
    # Use the widget value (handles both manual typing and browse)
    file_path = file_path.strip() if file_path else ''
    
    # --- Load file button ---
    if file_path:
        col_load, col_clear = st.columns([1, 1])
        with col_load:
            load_clicked = st.button("📥 Load File", use_container_width=True, type="primary")
        with col_clear:
            if st.button("🗑️ Clear", key="clear_import", use_container_width=True):
                st.session_state._pending_file_path = ''
                st.session_state.imported_geodata = None
                st.session_state.uploaded_shapefile = None
                st.session_state.import_preview_ready = False
                st.session_state.feature_id_column = '(auto index)'
                st.rerun()
        
        if load_clicked:
            if not os.path.exists(file_path):
                st.error(f"❌ File not found: {file_path}")
            else:
                with st.spinner("Loading geometry file..."):
                    try:
                        result = geometry_service.parse_file(file_path)
                        st.session_state.imported_geodata = result
                        st.session_state.uploaded_shapefile = file_path
                        st.session_state.import_preview_ready = False  # Reset preview
                        st.session_state.feature_id_column = '(auto index)'  # Reset on new file
                        st.rerun()
                    except ValueError as e:
                        st.error(f"❌ {e}")
                        st.session_state.imported_geodata = None
    
    # --- Display results if we have imported metadata ---
    imported = st.session_state.get('imported_geodata')
    if imported:
        geo_type = imported['type']
        n_features = imported['n_features']
        
        if geo_type == 'points':
            st.success(f"✅ Detected: **{n_features} Point(s)**")
        else:
            st.success(f"✅ Detected: **{n_features} Shape(s)** ({', '.join(imported['geom_types'])})")
        
        # --- Feature ID Column selector ---
        file_columns = imported.get('columns', [])
        id_options = ['(auto index)'] + file_columns
        current_selection = st.session_state.get('feature_id_column', '(auto index)')
        # Ensure the stored selection is still valid for the current file
        if current_selection not in id_options:
            current_selection = '(auto index)'
        selected_id_col = st.selectbox(
            "Feature ID Column",
            options=id_options,
            index=id_options.index(current_selection),
            key="feature_id_column",
            help="Choose which column to use as the feature identifier in the output CSV. "
                 "'(auto index)' uses a numeric 1, 2, 3… index."
        )
        
        # --- Button-triggered map preview (reads file on-demand) ---
        if st.button("🔍 Preview on Map", key="preview_import_map", use_container_width=True):
            st.session_state.import_preview_ready = True
        
        if st.session_state.get('import_preview_ready'):
            st.markdown("**Import Preview:**")
            import_path = st.session_state.get('uploaded_shapefile', '')
            if import_path and os.path.exists(import_path):
                try:
                    # Load file on-demand just for preview (simplified for rendering speed)
                    gdf = geometry_service.load_file(import_path, simplify_tolerance=0.005)
                    
                    centroid = gdf.geometry.unary_union.centroid
                    center = [centroid.y, centroid.x]
                    m = create_base_map(center=center, zoom=5)

                    if geo_type == 'points':
                        # Flatten MultiPoint geometries into individual points
                        flat_points = []
                        for _, row in gdf.iterrows():
                            pt = row.geometry
                            if pt.geom_type == 'MultiPoint':
                                flat_points.extend(pt.geoms)
                            else:
                                flat_points.append(pt)
                        add_markers(m, flat_points, color='blue')
                    else:
                        geojson_data = gdf_to_geojson(gdf)
                        add_geojson_overlay(m, geojson_data)
                    
                    render_map_display(m, key="import_preview_map", fit_bounds=gdf.total_bounds)
                except Exception as map_err:
                    st.warning(f"Map preview unavailable: {str(map_err)[:100]}")


def render_gadm_input():
    """GADM administrative boundary selection with map visualization."""
    st.subheader("GADM Administrative Boundaries")
    
    # Check pygadm availability
    try:
        import pygadm  # noqa: F401 — availability check only
    except ImportError:
        st.error("pygadm is not installed. Run: pip install pygadm")
        return
    
    # Country selection
    country = st.text_input(
        "Country Name", 
        placeholder="e.g., Italy, South Africa, Zambia", 
        key="gadm_country",
        help="Enter the country name to load its boundaries"
    )
    
    # Admin level for content
    admin_level = st.selectbox(
        "Administrative Level",
        options=[0, 1, 2],
        format_func=lambda x: f"Level {x} ({'Country' if x == 0 else 'State/Province' if x == 1 else 'District'})",
        key="gadm_level"
    )
    
    # Load / Clear buttons
    if country:
        col1, col2 = st.columns([1, 1])
        with col1:
            load_clicked = st.button("🔍 Load Boundary", use_container_width=True)
        with col2:
            clear_clicked = st.button("🗑️ Clear", use_container_width=True)
        
        if clear_clicked:
            # Reset all GADM state so the map disappears
            for key in ['gadm_gdf', 'gadm_country_loaded', 'gadm_level_loaded',
                        'gadm_selection', 'gadm_map_visible']:
                st.session_state.pop(key, None)
            return
        
        # --- Data loading phase: only runs on explicit user action ---
        country_changed = st.session_state.get('gadm_country_loaded') != country
        level_changed = st.session_state.get('gadm_level_loaded') != admin_level
        
        if load_clicked:
            # User explicitly requested a load — always proceed
            try:
                with st.spinner(f"Loading {country} boundaries..."):
                    gdf = fetch_gadm_boundaries(country, admin_level)
                
                # Store in session state
                st.session_state.gadm_gdf = gdf
                st.session_state.gadm_country_loaded = country
                st.session_state.gadm_level_loaded = admin_level
                st.session_state.gadm_map_visible = True
                
            except ValueError as e:
                st.error(f"❌ Could not find '{country}'. Check spelling.")
                st.caption(f"Error: {str(e)[:200]}")
                return
            except Exception as e:
                st.error(f"❌ Error loading boundary: {str(e)}")
                return
        
        elif country_changed or level_changed:
            # Inputs changed but user didn't click Load — clear stale data and prompt
            if st.session_state.get('gadm_gdf') is not None:
                st.info("Country or admin level changed. Click **🔍 Load Boundary** to reload.")
                for key in ['gadm_gdf', 'gadm_country_loaded', 'gadm_level_loaded',
                            'gadm_selection', 'gadm_map_visible']:
                    st.session_state.pop(key, None)
                return
        
        # --- Rendering phase: only when data is loaded and map is visible ---
        gdf = st.session_state.get('gadm_gdf')
        if gdf is not None and st.session_state.get('gadm_map_visible'):
            try:
                # Show info
                loaded_country = st.session_state.get('gadm_country_loaded', country)
                st.success(f"✅ Loaded {len(gdf)} region(s) for {loaded_country}")
                
                # If we have subdivisions, let user select specific ones
                loaded_level = st.session_state.get('gadm_level_loaded', 0)
                if loaded_level > 0 and len(gdf) > 1:
                    name_col = f"NAME_{loaded_level}"
                    if name_col in gdf.columns:
                        region_names = gdf[name_col].tolist()
                        
                        selected_regions = st.multiselect(
                            f"Select specific regions (optional)",
                            options=region_names,
                            default=[],
                            key="gadm_regions",
                            help="Leave empty to use entire country"
                        )
                        
                        if selected_regions:
                            gdf = gdf[gdf[name_col].isin(selected_regions)]
                            st.info(f"Selected {len(gdf)} region(s)")
                
                # Display map
                st.markdown("**Boundary Preview:**")
                
                try:
                    # Get centroid for map center
                    centroid = gdf.geometry.unary_union.centroid
                    center = [centroid.y, centroid.x]
                    
                    # Create folium map with satellite base
                    m = create_base_map(center=center, zoom=5)

                    # Convert to GeoJSON and detect display columns
                    geojson_data = gdf_to_geojson(gdf)
                    display_cols = extract_gadm_display_columns(geojson_data)
                    
                    # Build tooltip: use the most specific NAME column
                    tooltip_fields = None
                    tooltip_aliases = None
                    name_cols = [c for c in display_cols if "NAME" in c]
                    if name_cols:
                        tooltip_fields = [name_cols[-1]]
                        tooltip_aliases = ["Region:"]
                    
                    # Build popup: show all NAME/GID columns
                    popup_fields = display_cols if display_cols else None
                    
                    # Add GeoJson layer with interactivity
                    add_geojson_overlay(
                        m, geojson_data,
                        tooltip_fields=tooltip_fields,
                        tooltip_aliases=tooltip_aliases,
                        popup_fields=popup_fields,
                    )
                    
                    # Render map
                    render_map_display(m, key="gadm_map", fit_bounds=gdf.total_bounds)
                    
                except Exception as map_error:
                    st.warning(f"Map preview unavailable: {str(map_error)[:100]}")
                
                # Store selection in session state for export pipeline
                st.session_state.gadm_selection = {
                    'name': loaded_country,
                    'admin_level': loaded_level,
                    'gdf': gdf
                }
                
            except Exception as e:
                st.error(f"❌ Error displaying boundary: {str(e)}")
    else:
        st.caption("Enter a country name above to load its boundaries")


def render_verification_map():
    """Passive verification map showing selected geometries."""
    # This is shown in the point input for now
    pass


def render_time_section(loaded_settings: dict):
    """Section 3: Time Definition with form to reduce reruns."""
    st.header("3️⃣ Time Definition")
    
    # Derive minimum selectable year from the selected satellite's start date
    selected_satellite = st.session_state.get('selected_satellite')
    if selected_satellite and selected_satellite.get('startDate'):
        sat_min_year = int(selected_satellite['startDate'][:4])
    else:
        sat_min_year = 1980

    # Check for invalid date ranges in session state
    current_start = st.session_state.get('start_year', 2020)
    current_end = st.session_state.get('end_year', datetime.now().year)
    
    if current_end < current_start:
        st.error(f"⚠️ **Invalid Date Range**: End Year ({current_end}) is before Start Year ({current_start}).")

    # Seasonality filter - OUTSIDE form to allow instant UI update
    use_season = st.checkbox("🌾 Filter by Season (Day of Year)", 
                             value=st.session_state.get('use_season', loaded_settings.get('dates', {}).get('use_season', False)),
                             key="use_season_interactive")
    
    # Sync use_season to session state immediately
    st.session_state.use_season = use_season

    with st.form("time_definition_form"):
        col1, col2 = st.columns(2)
        
        with col1:
            start_year = st.number_input(
                "Start Year",
                min_value=sat_min_year,
                max_value=datetime.now().year,
                value=max(loaded_settings.get('dates', {}).get('start_year', 2020), sat_min_year),
                key="form_start_year"
            )
        
        with col2:
            end_year = st.number_input(
                "End Year",
                min_value=sat_min_year,
                max_value=datetime.now().year,
                value=max(loaded_settings.get('dates', {}).get('end_year', datetime.now().year), sat_min_year),
                key="form_end_year"
            )
        
        start_doy = 1
        end_doy = 365
        
        if use_season:
            col1, col2 = st.columns(2)
            with col1:
                start_doy = st.slider(
                    "Start DOY",
                    min_value=1,
                    max_value=365,
                    value=st.session_state.get('start_doy', loaded_settings.get('dates', {}).get('start_doy', 1)),
                    key="form_start_doy"
                )
            with col2:
                end_doy = st.slider(
                    "End DOY",
                    min_value=1,
                    max_value=365,
                    value=st.session_state.get('end_doy', loaded_settings.get('dates', {}).get('end_doy', 365)),
                    key="form_end_doy"
                )
            
            if start_doy > end_doy:
                st.info("💡 **Cross-Year Season**: This period will handle date ranges spanning across consecutive years.")

        # Form submit button
        submitted = st.form_submit_button("✅ Apply Date Settings", use_container_width=True)
        
        if submitted:
            # Update session state with form values
            st.session_state.start_year = start_year
            st.session_state.end_year = end_year
            # use_season is already updated via its own key/interactive widget
            st.session_state.start_doy = start_doy
            st.session_state.end_doy = end_doy
            
            # Also update date_config for the extraction service
            st.session_state.date_config = {
                'start_year': start_year,
                'end_year': end_year,
                'start_doy': start_doy,
                'end_doy': end_doy,
                'use_season': use_season
            }
            
            # Update the custom filename based on new dates
            update_default_filename()
            
            st.success("Dates and Filename updated!")
            st.rerun()

    # If no date_config yet, initialize it from session state or defaults
    if 'date_config' not in st.session_state:
        st.session_state.date_config = {
            'start_year': st.session_state.get('start_year', 2020),
            'end_year': st.session_state.get('end_year', datetime.now().year),
            'start_doy': st.session_state.get('start_doy', 1),
            'end_doy': st.session_state.get('end_doy', 365),
            'use_season': st.session_state.get('use_season', False)
        }


def render_execution_section(settings_service: SettingsService, satellites: list):
    """Section 4: Execution - Run extraction."""
    st.header("4️⃣ Execution")
    
    # Export method
    export_method = st.radio(
        "Export Method",
        options=["☁️ Save to Google Drive (Batch)", "💾 Download Locally (Interactive)"],
        horizontal=True,
        key="export_method"
    )
    
    # Drive folder configuration
    if "Drive" in export_method:
        drive_folder = settings_service.get_setting("gee", "drive_folder", "GEE_Exports")
        drive_folder = st.text_input("Drive Folder Name", value=drive_folder, key="drive_folder")
    
    # Filename / Task Name
    selected_satellite = st.session_state.get('selected_satellite')
    date_config = st.session_state.get('date_config', {})
    
    # Generate default suggestion
    sat_id = selected_satellite['id'] if selected_satellite else "GEE"
    year_str = f"{date_config.get('start_year', '')}_{date_config.get('end_year', '')}" if date_config else "extraction"
    default_filename = f"{sat_id}_{year_str}_timeseries"
    
    custom_filename = st.text_input(
        "File Name / Task Name", 
        value=default_filename, 
        help="The name used for the GEE task and the output file",
        key="custom_filename"
    )
    
    st.divider()
    
    # Run button
    if st.button("🚀 RUN EXTRACTION", type="primary", use_container_width=True):
        run_extraction(settings_service, export_method)


def run_extraction(settings_service: SettingsService, export_method: str):
    """Execute the GEE extraction - outputs CSV with time-series data."""
    with st.spinner("Submitting task to Google Earth Engine..."):
        try:
            # Validate inputs
            selected_satellite = st.session_state.get('selected_satellite')
            selected_bands = st.session_state.get('selected_bands', [])
            selected_points = st.session_state.get('selected_points', [])
            date_config = st.session_state.get('date_config', {})
            band_selections = st.session_state.get('band_selections', {})
            
            if not selected_satellite:
                st.error("Please select a satellite/dataset")
                return
            
            if not selected_bands:
                st.error("Please select at least one band")
                return
            
            if not selected_points and not st.session_state.get('uploaded_shapefile') and not st.session_state.get('gadm_selection'):
                st.error("Please define a region of interest (point, shapefile, or GADM)")
                return
            
            # Initialize GEE if needed
            project_id = settings_service.get_setting("gee", "project_id")
            try:
                ee.Initialize(project=project_id)
            except:
                st.error("Failed to initialize GEE. Please check authentication.")
                return
            
            # Build geometry and feature collection
            geometry, features = build_geometry_and_features()
            if geometry is None:
                st.error("Failed to build geometry from inputs")
                return
            
            # Build image collection
            collection_id = selected_satellite['ee_collection_name']
            collection = ee.ImageCollection(collection_id)
            
            # Apply date filters
            start_date = f"{date_config['start_year']}-01-01"
            end_date = f"{date_config['end_year']}-12-31"
            collection = collection.filterDate(start_date, end_date)
            
            # Apply DOY filter if not full year
            if date_config.get('start_doy', 1) != 1 or date_config.get('end_doy', 365) != 365:
                start_doy = date_config['start_doy']
                end_doy = date_config['end_doy']
                
                if start_doy <= end_doy:
                    # Normal season
                    collection = collection.filter(ee.Filter.dayOfYear(start_doy, end_doy))
                else:
                    # Cross-year season
                    collection = collection.filter(
                        ee.Filter.Or(
                            ee.Filter.dayOfYear(start_doy, 365),
                            ee.Filter.dayOfYear(1, end_doy)
                        )
                    )
            
            # Filter by bounds
            collection = collection.filterBounds(geometry)
            
            # Select only needed bands
            collection = collection.select(selected_bands)
            
            # Build the reducer based on band selections
            # We'll use the same reducer for all bands (most common case)
            # or combine multiple reducers
            reducer_name = list(band_selections.values())[0] if band_selections else 'mean'
            
            if reducer_name == 'mean':
                reducer = ee.Reducer.mean()
            elif reducer_name == 'sum':
                reducer = ee.Reducer.sum()
            elif reducer_name == 'max':
                reducer = ee.Reducer.max()
            elif reducer_name == 'min':
                reducer = ee.Reducer.min()
            elif reducer_name == 'median':
                reducer = ee.Reducer.median()
            elif reducer_name == 'first':
                reducer = ee.Reducer.first()
            else:
                reducer = ee.Reducer.mean()
            
            # Detect whether the selected dataset has sub-daily (hourly) cadence
            is_hourly = selected_satellite.get('isHourly', False)

            # Function to extract data for each image
            def extract_values(image):
                """Extract values at each point/region for an image.

                Uses per-feature reduceRegion instead of reduceRegions.

                Why: ECMWF/ERA5_LAND/HOURLY assets switched longitude origin from
                -180.05 to -360.05 during 2024-01-11 (stable from 2024-01-12).
                With the -360.05 grid, Image.reduceRegions / sampleRegions on
                point geometries can silently return nulls, while
                Image.reduceRegion still samples correctly. Mapping reduceRegion
                over features is the standard per-feature equivalent and works
                for all datasets/geometries we use (not an ERA5-only branch).
                """
                date = ee.Date(image.get('system:time_start'))
                scale = selected_satellite.get('pixelSize', 1000)

                def reduce_feature(feature):
                    # Keep scale fixed (no bestEffort): bestEffort can silently
                    # coarsen large polygons and change zonal statistics.
                    stats = image.reduceRegion(
                        reducer=reducer,
                        geometry=feature.geometry(),
                        scale=scale,
                        maxPixels=1e13,
                    )

                    props = {
                        'date': date.format('YYYY-MM-dd'),
                        'year': date.get('year'),
                        'month': date.get('month'),
                        'day': date.get('day'),
                        'doy': date.getRelative('day', 'year').add(1),
                        'system_time': image.get('system:time_start')
                    }
                    if is_hourly:
                        # Store only the time part (HH:mm) so sub-daily slots
                        # (e.g. 00:00 vs 00:30 for IMERG 30-min) are distinguishable
                        # without duplicating the date column.
                        props['time'] = date.format('HH:mm')

                    # reduceRegion keys outputs by band name for simple reducers
                    # (mean/sum/min/max/median/first), for both single- and
                    # multi-band images.
                    for band in selected_bands:
                        props[band] = stats.get(band)

                    return feature.set(props)

                return features.map(reduce_feature)
            
            # Map over collection to extract values
            extracted = collection.map(extract_values).flatten()

            # Build a logical column order for the output CSV -------------
            _time_cols = ['date']
            if is_hourly:
                _time_cols.append('time')
            _time_cols += ['year', 'month', 'day', 'doy']

            _spatial_cols = []
            _imported = st.session_state.get('imported_geodata')
            if selected_points:
                _spatial_cols = ['point_id', 'latitude', 'longitude']
            elif _imported and _imported.get('type') == 'points':
                _spatial_cols = ['point_id', 'latitude', 'longitude']
            elif st.session_state.get('gadm_selection') and 'gdf' in st.session_state.get('gadm_selection', {}):
                _gadm_gdf = st.session_state['gadm_selection']['gdf']
                _gadm_name_gid = [c for c in _gadm_gdf.columns
                                   if c.startswith(('GID_', 'NAME_'))]
                _spatial_cols = ['feature_id', 'country', 'admin_level', 'source'] + _gadm_name_gid
            elif _imported and _imported.get('type') == 'shapes':
                _spatial_cols = ['feature_id', 'source']
            else:
                _spatial_cols = ['source']

            ordered_columns = (
                ['system:index'] + _time_cols + _spatial_cols
                + selected_bands + ['system_time']
            )
            # ---------------------------------------------------------------

            # Export task name (use custom filename if provided)
            task_name = st.session_state.get('custom_filename', f"{selected_satellite['id']}_{date_config['start_year']}_{date_config['end_year']}_timeseries")
            
            if "Drive" in export_method:
                drive_folder = st.session_state.get('drive_folder', 'GEE_Exports')
                
                # Export as CSV to Drive
                task = ee.batch.Export.table.toDrive(
                    collection=extracted,
                    description=task_name,
                    folder=drive_folder,
                    fileNamePrefix=task_name,
                    fileFormat='CSV',
                    selectors=ordered_columns
                )
                task.start()
                
                # Get task ID
                task_id = task.status()['id']
                
                st.success(f"✅ Task submitted successfully!")
                st.info(f"**Task ID:** `{task_id}`")
                st.info(f"**Output:** CSV file in Google Drive folder `{drive_folder}`")
                st.markdown(f"📊 [View in GEE Console](https://code.earthengine.google.com/tasks)")
                
                # Save to history
                # Save to history
                history_manager = HistoryManager()
                history_entry = {
                    'satellite': selected_satellite['id'],
                    'bands': selected_bands,
                    'reducers': band_selections,
                    'geometry_source': 'Points' if selected_points else ('Shapefile' if st.session_state.get('uploaded_shapefile') else 'GADM'),
                    'num_points': len(selected_points) if selected_points else 0,
                    'selected_points': selected_points,  # Save full points list
                    'selected_points': selected_points,  # Save full points list
                    'gadm_selection': {k: v for k, v in st.session_state.get('gadm_selection', {}).items() if k != 'gdf'}, # Save GADM details without GDF
                    'gadm_regions': st.session_state.get('gadm_regions'), # Save specific regions if any
                    'uploaded_shapefile': st.session_state.get('uploaded_shapefile'), # Save shapefile path
                    'dates': date_config,
                    'export_method': 'Drive',
                    'output_format': 'CSV',
                    'task_id': task_id,
                    'custom_filename': task_name
                }
                history_manager.add_entry(history_entry)
                
            else:
                # Local download - get as CSV directly
                # Note: This may fail for very large datasets
                st.info("Fetching data... This may take a moment for large datasets.")
                
                try:
                    # Get the data directly (limited to ~5000 features)
                    data = extracted.getInfo()
                    
                    if data and 'features' in data:
                        # Convert to pandas dataframe
                        import pandas as pd
                        
                        rows = []
                        for feature in data['features']:
                            props = feature.get('properties', {})
                            rows.append(props)
                        
                        df = pd.DataFrame(rows)

                        # Reorder columns into a logical order;
                        # preserve any unexpected extra columns at the end.
                        _extra = [c for c in df.columns
                                  if c not in ordered_columns]
                        _final_cols = [c for c in ordered_columns + _extra
                                       if c in df.columns]
                        df = df[_final_cols]

                        # Provide download button
                        csv_data = df.to_csv(index=False)
                        st.success("✅ Data extracted successfully!")
                        st.download_button(
                            label="📥 Download CSV",
                            data=csv_data,
                            file_name=f"{task_name}.csv",
                            mime="text/csv"
                        )
                        
                        # Show preview
                        st.subheader("Data Preview")
                        st.dataframe(df.head(20))
                    else:
                        st.warning("No data returned. Try adjusting filters or using Drive export for large datasets.")
                        
                except Exception as fetch_error:
                    st.error(f"Local fetch failed (dataset may be too large): {fetch_error}")
                    st.info("💡 Tip: Use 'Save to Google Drive' for larger datasets")
        
        except Exception as e:
            st.error(f"❌ Extraction failed: {str(e)}")
            import traceback
            st.code(traceback.format_exc())


def build_geometry_and_features():
    """Build ee.Geometry and ee.FeatureCollection from session state inputs."""
    geometry_service = GeometryService()
    
    # Check for manual points
    points = st.session_state.get('selected_points', [])
    if points:
        # Create features with point IDs
        features = []
        for i, p in enumerate(points):
            point = ee.Geometry.Point([p['lon'], p['lat']])
            feature = ee.Feature(point, {
                'point_id': i + 1,
                'latitude': p['lat'],
                'longitude': p['lon']
            })
            features.append(feature)
        
        feature_collection = ee.FeatureCollection(features)
        
        if len(points) == 1:
            geometry = ee.Geometry.Point([points[0]['lon'], points[0]['lat']])
        else:
            coords = [[p['lon'], p['lat']] for p in points]
            geometry = ee.Geometry.MultiPoint(coords)
        
        return geometry, feature_collection
    
    # Check for imported file (lazy: reads file on-demand, not from session state)
    imported = st.session_state.get('imported_geodata')
    import_path = st.session_state.get('uploaded_shapefile')
    if imported and import_path:
        geo_type = imported['type']
        
        # Load file on-demand with simplification for shapes
        simplify = 0.01 if geo_type == 'shapes' else 0.0
        gdf = geometry_service.load_file(import_path, simplify_tolerance=simplify)
        
        # Determine which column to use as feature identifier
        id_col = st.session_state.get('feature_id_column', '(auto index)')
        use_column = id_col != '(auto index)' and id_col in gdf.columns
        
        if geo_type == 'points':
            # Treat as individual points (like manual point entry)
            features = []
            point_id = 1
            for _, row in gdf.iterrows():
                geom = row.geometry
                if geom.geom_type == 'MultiPoint':
                    for sub_pt in geom.geoms:
                        props = {
                            'point_id': row[id_col] if use_column else point_id,
                            'latitude': sub_pt.y,
                            'longitude': sub_pt.x
                        }
                        feature = ee.Feature(
                            ee.Geometry.Point([sub_pt.x, sub_pt.y]), props
                        )
                        features.append(feature)
                        point_id += 1
                else:
                    props = {
                        'point_id': row[id_col] if use_column else point_id,
                        'latitude': geom.y,
                        'longitude': geom.x
                    }
                    feature = ee.Feature(
                        ee.Geometry.Point([geom.x, geom.y]), props
                    )
                    features.append(feature)
                    point_id += 1
            
            feature_collection = ee.FeatureCollection(features)
            geometry = feature_collection.geometry()
            return geometry, feature_collection
        
        else:
            # Treat as shapes (polygons / lines) — already simplified
            features = []
            for idx, row in enumerate(gdf.itertuples()):
                geom_dict = row.geometry.__geo_interface__
                ee_geom = ee.Geometry(geom_dict)
                # Use selected column value or numeric index as feature_id
                if use_column:
                    fid = getattr(row, id_col, idx + 1)
                else:
                    fid = idx + 1
                feature = ee.Feature(ee_geom, {
                    'feature_id': fid,
                    'source': 'shapefile'
                })
                features.append(feature)
            
            feature_collection = ee.FeatureCollection(features)
            geometry = feature_collection.geometry()
            return geometry, feature_collection
    
    # Legacy fallback: Check for shapefile path without parsed geodata
    shapefile_path = st.session_state.get('uploaded_shapefile')
    if shapefile_path and not imported:
        geometry = geometry_service.parse_geometry(shapefile_path, 'shapefile')
        feature_collection = ee.FeatureCollection([ee.Feature(geometry, {'source': 'shapefile'})])
        return geometry, feature_collection
    
    # Check for GADM
    gadm_selection = st.session_state.get('gadm_selection')
    if gadm_selection and 'gdf' in gadm_selection:
        # Use the cached GeoDataFrame
        gdf = gadm_selection['gdf']
        
        # WORKAROUND for pygadm pandas compatibility bug:
        # Cannot use gdf.__geo_interface__ or subset the gdf
        # Instead, access geometry series directly and build EE features manually
        
        # Simplify geometries to avoid GEE payload limits
        simplified_geoms = gdf.geometry.simplify(tolerance=0.01)
        
        # Build EE features from each geometry
        ee_features = []
        
        # Helper to convert numpy/pandas types to standard python types for JSON serialization
        def convert_types(obj):
            if isinstance(obj, (int, float, str, bool, type(None))):
                return obj
            if hasattr(obj, 'item'): 
                return obj.item()
            return str(obj)

        for i in range(len(gdf)):
            # Get geometry
            geom = simplified_geoms.iloc[i]
            
            # Get properties for this feature
            # We use the original gdf to get properties
            props = gdf.iloc[i].drop('geometry').to_dict()
            
            # Filter and sanitise properties
            # User request: Keep only GID_* and NAME_* columns from GADM
            clean_props = {}
            for k, v in props.items():
                # Check if column starts with GID_ or NAME_
                if k.startswith(('GID_', 'NAME_')):
                    clean_props[k] = convert_types(v)
            
            # Add basic metadata
            clean_props.update({
                'source': 'gadm',
                'feature_id': i + 1,
                'country': gadm_selection.get('name', ''),
                'admin_level': gadm_selection.get('admin_level', 0)
            })

            # Convert shapely geometry to GeoJSON dict
            geom_dict = geom.__geo_interface__
            
            # Create EE geometry from GeoJSON
            ee_geom = ee.Geometry(geom_dict)
            
            # Create feature with all properties
            ee_feature = ee.Feature(ee_geom, clean_props)
            ee_features.append(ee_feature)
        
        # Create FeatureCollection
        feature_collection = ee.FeatureCollection(ee_features)
        
        # Get geometry as union of all features
        geometry = feature_collection.geometry()
        
        return geometry, feature_collection
    elif gadm_selection:
        # Fallback to GeometryService if no gdf in selection
        geometry = geometry_service.parse_geometry(gadm_selection, 'gadm')
        feature_collection = ee.FeatureCollection([ee.Feature(geometry, {
            'source': 'gadm',
            'country': gadm_selection.get('name', ''),
            'admin_level': gadm_selection.get('admin_level', 0)
        })])
        return geometry, feature_collection
    
    return None, None

