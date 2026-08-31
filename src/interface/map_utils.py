"""
Map utility helpers for SatLit.

Provides reusable functions for creating Folium maps with consistent
base layers, overlays, rendering via st_folium, and drag-to-resize.
"""
import folium
import json
from jinja2 import Template
from streamlit_folium import st_folium


# --- Constants ---

ESRI_SATELLITE_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/{z}/{y}/{x}"
)

DEFAULT_STYLE = {
    'fillColor': '#3388ff',
    'color': '#3388ff',
    'weight': 2,
    'fillOpacity': 0.3,
}

DEFAULT_HIGHLIGHT = {
    'fillColor': '#ffcc00',
    'color': '#ffcc00',
    'weight': 3,
    'fillOpacity': 0.6,
}


# --- Builder functions ---

def create_base_map(center=None, zoom=5):
    """Create a Folium map with the Esri satellite base layer.

    Args:
        center: [lat, lon] center point. Defaults to [0, 0].
        zoom: Initial zoom level.

    Returns:
        folium.Map with satellite tiles added.
    """
    if center is None:
        center = [0, 0]

    m = folium.Map(location=center, zoom_start=zoom)

    folium.TileLayer(
        tiles=ESRI_SATELLITE_URL,
        attr="Esri",
        name="Satellite",
        overlay=False,
        control=True,
    ).add_to(m)

    return m


def add_geojson_overlay(m, geojson_data, style=None, highlight=None,
                        tooltip_fields=None, tooltip_aliases=None,
                        popup_fields=None, popup_aliases=None):
    """Add a GeoJSON overlay to a Folium map.

    Args:
        m: folium.Map to add the layer to.
        geojson_data: GeoJSON dict (from json.loads(gdf.to_json())).
        style: Optional style_function dict. Uses DEFAULT_STYLE if None.
        highlight: Optional highlight_function dict. Uses DEFAULT_HIGHLIGHT if None.
        tooltip_fields: List of field names for GeoJsonTooltip.
        tooltip_aliases: List of aliases for GeoJsonTooltip.
        popup_fields: List of field names for GeoJsonPopup.
        popup_aliases: List of aliases for GeoJsonPopup.

    Returns:
        The folium.GeoJson object added to the map.
    """
    style = style or DEFAULT_STYLE
    highlight = highlight or DEFAULT_HIGHLIGHT

    tooltip = None
    if tooltip_fields:
        tooltip = folium.GeoJsonTooltip(
            fields=tooltip_fields,
            aliases=tooltip_aliases or tooltip_fields,
            style="font-weight: bold; color: #333;",
            sticky=True,
        )

    popup = None
    if popup_fields:
        popup = folium.GeoJsonPopup(
            fields=popup_fields,
            aliases=popup_aliases or popup_fields,
            localize=True,
        )

    geojson_layer = folium.GeoJson(
        geojson_data,
        control=False,
        style_function=lambda x: style,
        highlight_function=lambda x: highlight,
        tooltip=tooltip,
        popup=popup,
    )
    geojson_layer.add_to(m)

    return geojson_layer


def add_markers(m, points, color='red', icon='info-sign', label_format=None):
    """Add point markers to a Folium map.

    Args:
        m: folium.Map to add markers to.
        points: List of dicts with 'lat' and 'lon' keys,
                or list of shapely Point geometries.
        color: Marker color.
        icon: Marker icon name.
        label_format: Optional callable(i, point) -> str for popup label.
                      Defaults to "Point {i+1}: ({lat}, {lon})".

    Returns:
        The map (modified in-place).
    """
    for i, pt in enumerate(points):
        if hasattr(pt, 'y') and hasattr(pt, 'x'):
            # Shapely geometry
            lat, lon = pt.y, pt.x
        else:
            lat, lon = pt['lat'], pt['lon']

        if label_format:
            label = label_format(i, pt)
        else:
            label = f"Point {i+1}: ({lat:.4f}, {lon:.4f})"

        folium.Marker(
            [lat, lon],
            popup=label,
            icon=folium.Icon(color=color, icon=icon),
        ).add_to(m)

    return m


def render_map(m, key, height=800, width=None, fit_bounds=None):
    """Finalize and render a Folium map in Streamlit.

    Adds LayerControl, optionally fits bounds, and calls st_folium.

    Args:
        m: folium.Map to render.
        key: Streamlit widget key for st_folium.
        height: Map height in pixels.
        width: Map width (None = auto).
        fit_bounds: Optional [minx, miny, maxx, maxy] bounds to fit.
                    If provided, calls m.fit_bounds.

    Returns:
        The st_folium return value (map_data dict).
    """
    if fit_bounds is not None:
        # fit_bounds expects [[south, west], [north, east]]
        bounds = fit_bounds  # [minx, miny, maxx, maxy]
        m.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])

    folium.LayerControl(position="topright", collapsed=False).add_to(m)

    DragHandlePlugin(map_key=key).add_to(m)

    return st_folium(m, height=height, width=width, key=key)


def render_map_display(m, key, height=800, width=None, fit_bounds=None):
    """Render a Folium map in display-only mode — no reruns on interaction.

    Uses st_folium with returned_objects=[] so the map is fully interactive
    (pan, zoom, click popups) but does NOT trigger Streamlit reruns.
    Ideal for preview/verification maps (GADM, shapefile).

    Args:
        m: folium.Map to render.
        key: Streamlit widget key for st_folium.
        height: Map height in pixels.
        width: Map width (None = auto).
        fit_bounds: Optional [minx, miny, maxx, maxy] bounds to fit.

    Returns:
        None (no interaction data is returned).
    """
    if fit_bounds is not None:
        bounds = fit_bounds
        m.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])

    folium.LayerControl(position="topright", collapsed=False).add_to(m)

    DragHandlePlugin(map_key=key).add_to(m)

    st_folium(m, height=height, width=width, key=key, returned_objects=[])


def gdf_to_geojson(gdf):
    """Convert a GeoDataFrame to a GeoJSON dict suitable for Folium.

    Handles the pygadm subclass issue by casting to a vanilla GeoDataFrame
    before serialization.

    Args:
        gdf: GeoDataFrame (may be a pygadm subclass).

    Returns:
        dict: Parsed GeoJSON.
    """
    import geopandas as gpd
    gdf_vanilla = gpd.GeoDataFrame(gdf, geometry='geometry')
    return json.loads(gdf_vanilla.to_json())


def extract_gadm_display_columns(geojson_data, max_level=3):
    """Extract NAME_X and GID_X columns from GADM GeoJSON properties.

    Args:
        geojson_data: Parsed GeoJSON dict from gdf_to_geojson.
        max_level: Maximum admin level to check (0 to max_level).

    Returns:
        list: Column names like ['NAME_0', 'GID_0', 'NAME_1', 'GID_1', ...]
    """
    available_properties = []
    if 'features' in geojson_data and len(geojson_data['features']) > 0:
        first_props = geojson_data['features'][0]['properties']
        available_properties = list(first_props.keys())

    display_cols = []
    for i in range(max_level + 1):
        name_col = f"NAME_{i}"
        if name_col in available_properties:
            display_cols.append(name_col)
        gid_col = f"GID_{i}"
        if gid_col in available_properties:
            display_cols.append(gid_col)

    return display_cols


def fetch_gadm_boundaries(country, admin_level):
    """Fetch GADM boundaries via pygadm, with Streamlit caching.

    Args:
        country: Country name string.
        admin_level: Administrative level (0, 1, or 2).

    Returns:
        GeoDataFrame with boundary geometries.
    """
    import pygadm
    import streamlit as st

    @st.cache_data(show_spinner=False)
    def _fetch(name, level):
        if level > 0:
            return pygadm.Items(name=name, content_level=level)
        else:
            return pygadm.Items(name=name)

    return _fetch(country, admin_level)


class DragHandlePlugin(folium.MacroElement):
    """Folium plugin that attaches a drag-to-resize handle to its map iframe.

    Injected once per map, eliminating global scripts and cross-map interference.
    """
    _template = Template("""
    {% macro script(this, kwargs) %}
    (function() {
        var iframe = window.frameElement;
        if (!iframe) return;

        var parentDoc = iframe.ownerDocument;
        var LS_KEY = 'folium_drag_height_{{this._map_key}}';
        var DEFAULT_HEIGHT = 800;
        var MIN_HEIGHT = 200;
        var MAX_HEIGHT = 800;

        function applyHeight(h) {
            h = Math.min(Math.max(h, MIN_HEIGHT), MAX_HEIGHT);
            var wrapper = iframe.parentElement;
            iframe.style.setProperty('height', h + 'px', 'important');
            if (wrapper) {
                wrapper.style.setProperty('height', 'auto', 'important');
                wrapper.style.setProperty('max-height', 'none', 'important');
                wrapper.style.setProperty('overflow', 'visible', 'important');
            }
        }

        function saveHeight(h) {
            try {
                window.parent.localStorage.setItem(LS_KEY, String(h));
            } catch (e) {}
        }

        function loadHeight() {
            try {
                var v = window.parent.localStorage.getItem(LS_KEY);
                if (v !== null) {
                    var h = parseInt(v, 10);
                    if (!isNaN(h)) return h;
                }
            } catch (e) {}
            return DEFAULT_HEIGHT;
        }

        function removeOldHandle() {
            var sibling = iframe.nextElementSibling;
            if (sibling && sibling.dataset && sibling.dataset.resizeHandle === '1') {
                sibling.remove();
            }
        }

        removeOldHandle();
        applyHeight(loadHeight());
        iframe.dataset.foliumHandled = '1';

        var handle = parentDoc.createElement('div');
        handle.title = 'Drag to resize map';
        handle.dataset.resizeHandle = '1';
        handle.style.cssText = [
            'height:8px',
            'background:#e8e8e8',
            'cursor:ns-resize',
            'border-radius:0 0 4px 4px',
            'display:flex',
            'align-items:center',
            'justify-content:center',
            'user-select:none',
            'touch-action:none',
            'transition:background 0.15s'
        ].join(';');

        var grip = parentDoc.createElement('div');
        grip.style.cssText = 'width:40px;height:3px;background:#aaa;border-radius:2px;';
        handle.appendChild(grip);

        iframe.parentNode.insertBefore(handle, iframe.nextSibling);

        handle.addEventListener('pointerdown', function(e) {
            if (!iframe.isConnected) { handle.remove(); return; }
            e.preventDefault();
            handle.setPointerCapture(e.pointerId);
            handle._dragStartY = e.clientY;
            handle._dragStartH = iframe.offsetHeight;
            handle.style.background = '#d0d0d0';
        });

        handle.addEventListener('pointermove', function(e) {
            if (handle._dragStartY == null) return;
            if (!iframe.isConnected) { handle.remove(); return; }
            var h = handle._dragStartH + e.clientY - handle._dragStartY;
            h = Math.min(Math.max(h, MIN_HEIGHT), MAX_HEIGHT);
            applyHeight(h);
            saveHeight(h);
        });

        handle.addEventListener('pointerup', function(e) {
            if (handle._dragStartY == null) return;
            handle.releasePointerCapture(e.pointerId);
            handle._dragStartY = null;
            handle._dragStartH = null;
            handle.style.background = '#e8e8e8';
        });

        handle.addEventListener('pointercancel', function() {
            handle._dragStartY = null;
            handle._dragStartH = null;
            handle.style.background = '#e8e8e8';
        });
    })();
    {% endmacro %}
    """)

    def __init__(self, map_key):
        super().__init__()
        self._name = 'DragHandlePlugin'
        self._map_key = map_key

