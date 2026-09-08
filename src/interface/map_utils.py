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

def create_base_map(center=None, zoom=5, basemap_choice=None, tiles=None):
    """Create a Folium map with selectable base layer.

    Uses tiles=None + two explicit TileLayers with ``show`` flag to
    control the active base layer (build order does NOT determine the
    visible layer). Exactly one base has show=True. Satellite is active
    by default; a persisted choice is honoured if provided.

    Args:
        center: [lat, lon] center point. Defaults to [0, 0].
        zoom: Initial zoom level.
        basemap_choice: "Satellite" or "Streets" / "OpenStreetMap".
            Takes precedence over ``tiles`` when provided.
        tiles: Optional alias for basemap_choice (back-compat).

    Returns:
        folium.Map with both base layers added; LayerControl must be
        added LAST via _finalize.
    """
    if center is None:
        center = [0, 0]

    # Normalize choice: basemap_choice > tiles > default Satellite
    raw = basemap_choice if basemap_choice is not None else tiles
    if raw is None:
        choice = "Satellite"
    else:
        choice = str(raw)
    low = choice.lower()
    is_satellite = low in ("satellite", "esri", "esri_satellite")

    m = folium.Map(
        location=center,
        zoom_start=zoom,
        tiles=None,
    )
    folium.TileLayer(
        tiles=ESRI_SATELLITE_URL,
        attr="Esri",
        name="Satellite",
        overlay=False,
        control=True,
        show=is_satellite,
    ).add_to(m)
    folium.TileLayer(
        tiles="OpenStreetMap",
        attr="OpenStreetMap",
        name="Streets",
        overlay=False,
        control=True,
        show=not is_satellite,
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


def create_points_feature_group(points, color='red', icon='info-sign', label_format=None):
    """Build a FeatureGroup with point markers (for st_folium feature_group_to_add).

    Reuses add_markers logic but does not mutate the base Map, so the
    folium base passed to st_folium stays structurally identical across
    reruns and never remounts.

    Args:
        points: List of dicts with 'lat' and 'lon' keys,
                or list of shapely Point geometries.
        color: Marker color.
        icon: Marker icon name.
        label_format: Optional callable(i, point) -> str.

    Returns:
        folium.FeatureGroup with markers added (or empty group if no points).
    """
    fg = folium.FeatureGroup(name="Selected Points")
    for i, pt in enumerate(points):
        if hasattr(pt, 'y') and hasattr(pt, 'x'):
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
        ).add_to(fg)
    return fg


# --- Internal finalizer (deduplicates render_map / render_map_display) ---

def _finalize(m, key, fit_bounds, add_layer_control=True):
    """Shared finalizer: fit bounds, add controls, attach drag handle.

    Keeps render_map and render_map_display DRY without changing their
    public signatures or return behaviour.
    """
    if fit_bounds is not None:
        # fit_bounds expects [[south, west], [north, east]]
        bounds = fit_bounds  # [minx, miny, maxx, maxy]
        m.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])

    if add_layer_control:
        folium.LayerControl(position="topright", collapsed=False).add_to(m)
    DragHandlePlugin(map_key=key).add_to(m)
    return m


def render_map(m, key, height=800, width=None, fit_bounds=None, add_layer_control=True, returned_objects=None, feature_group_to_add=None, center=None, zoom=None):
    """Finalize and render a Folium map in Streamlit.

    Adds LayerControl, optionally fits bounds, and calls st_folium.
    Supports the stable-base recipe: base map is structurally identical
    each rerun; point markers go via feature_group_to_add; center/zoom
    are imperative setView args applied once from pending_recenter.

    Args:
        m: folium.Map to render.
        key: Streamlit widget key for st_folium.
        height: Map height in pixels.
        width: Map width (None = auto).
        fit_bounds: Optional [minx, miny, maxx, maxy] bounds to fit.
                    If provided, calls m.fit_bounds.
        add_layer_control: Whether to add Leaflet LayerControl.
        returned_objects: Passed to st_folium. None = default (all).
                    Point map uses ["last_clicked"] to avoid pan/zoom reruns.
        feature_group_to_add: Optional folium.FeatureGroup passed to
                    st_folium so markers update without remounting base.
        center: Optional [lat, lon] imperative setView (from pending_recenter).
        zoom: Optional zoom imperative setView.

    Returns:
        The st_folium return value (map_data dict).
    """
    _finalize(m, key, fit_bounds, add_layer_control=add_layer_control)
    kwargs = {}
    if returned_objects is not None:
        kwargs["returned_objects"] = returned_objects
    if feature_group_to_add is not None:
        kwargs["feature_group_to_add"] = feature_group_to_add
    if center is not None:
        kwargs["center"] = center
    if zoom is not None:
        kwargs["zoom"] = zoom
    return st_folium(m, height=height, width=width, key=key, **kwargs)


def render_map_display(m, key, height=800, width=None, fit_bounds=None, add_layer_control=True):
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
        add_layer_control: Whether to add LayerControl (preview maps keep True).

    Returns:
        None (no interaction data is returned).
    """
    _finalize(m, key, fit_bounds, add_layer_control=add_layer_control)
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

    Why the handle lives OUTSIDE the iframe (sibling div in the parent
    document, not inside the Leaflet map):
    - Leaflet steals mouse events inside the iframe (dragging the map vs
      dragging the handle becomes ambiguous, pointer events get swallowed).
    - Streamlit re-renders the iframe on widget changes; any listeners
      injected *inside* the iframe are wiped when the iframe srcdoc reloads.
    - A sibling handle in the parent doc survives iframe redraws, receives
      pointer events reliably even when the cursor leaves the map, and can
      resize the iframe element itself without fighting the map's handlers.

    Resize strategy: update iframe.style.height + wrapper height, dispatch
    window resize, and call Leaflet invalidateSize() when the iframe is
    same-origin accessible (guarded try/catch for sandboxed/ cross-origin
    blocks). Moves are throttled with requestAnimationFrame; height is
    persisted to localStorage only once per drag-end (pointerup) keyed by
    map key so each map remembers its own height.
    """
    # ponytail: per-map localStorage (folium_drag_height_<key>) — no cross-tab sync, no debounce queue. Add BroadcastChannel sync if multi-tab live-resize needed.
    _template = Template("""
    {% macro script(this, kwargs) %}
    (function() {
        // --- Locate iframe (this script runs inside the iframe) ---
        var iframe = window.frameElement;
        if (!iframe) return;

        // Handle must live in parent document, not inside iframe.
        // See class docstring for rationale (Leaflet steals events, iframe redraw wipes listeners).
        var parentDoc = iframe.ownerDocument;
        // Streamlit mounts iframes under a wrapper; parentDoc is the outer doc.
        // For st_folium the iframe is inside parentDoc; use parentDoc.defaultView for parent window.
        var parentWin = parentDoc.defaultView || window.parent;

        var LS_KEY = 'folium_drag_height_{{this._map_key}}';
        var DEFAULT_HEIGHT = 800;
        var MIN_HEIGHT = 200;
        var MAX_HEIGHT = 800;

        // --- Persistence helpers ---
        function loadHeight() {
            try {
                var v = parentWin.localStorage.getItem(LS_KEY);
                if (v !== null) {
                    var h = parseInt(v, 10);
                    if (!isNaN(h)) return Math.min(Math.max(h, MIN_HEIGHT), MAX_HEIGHT);
                }
            } catch (e) {
                // localStorage blocked (private mode / sandbox) — fall back to default
            }
            return DEFAULT_HEIGHT;
        }

        function saveHeight(h) {
            try {
                parentWin.localStorage.setItem(LS_KEY, String(h));
            } catch (e) {
                // quota or access denied — silently ignore
            }
        }

        // --- Resize helper: update iframe + wrapper, then notify Leaflet ---
        function applyHeight(h) {
            h = Math.min(Math.max(h, MIN_HEIGHT), MAX_HEIGHT);
            var wrapper = iframe.parentElement;
            iframe.style.setProperty('height', h + 'px', 'important');
            if (wrapper) {
                wrapper.style.setProperty('height', 'auto', 'important');
                wrapper.style.setProperty('max-height', 'none', 'important');
                wrapper.style.setProperty('overflow', 'visible', 'important');
            }
            // Notify layout: dispatch resize on both windows so Streamlit + page react
            try { parentWin.dispatchEvent(new Event('resize')); } catch (e) {}
            try { window.dispatchEvent(new Event('resize')); } catch (e) {}
            // Ask Leaflet to recalc tiles — guarded for sandboxed / cross-origin iframe
            try {
                var innerDoc = iframe.contentDocument || (iframe.contentWindow && iframe.contentWindow.document);
                if (innerDoc) {
                    var leafletEl = innerDoc.querySelector('.leaflet-container');
                    // Leaflet stores map instance on container via _leaflet_id; try to find it
                    // Fallback: dispatch resize inside iframe so Leaflet's own handler fires
                    if (iframe.contentWindow) {
                        try { iframe.contentWindow.dispatchEvent(new Event('resize')); } catch (e2) {}
                    }
                    // If accessible, attempt invalidateSize via L.Map instances
                    if (leafletEl && iframe.contentWindow && iframe.contentWindow.L) {
                        // Leaflet maps are not directly reachable; trigger invalidate by resize event above.
                        // If _leaflet_map is attached (some plugins), use it:
                        if (leafletEl._leaflet_map && leafletEl._leaflet_map.invalidateSize) {
                            leafletEl._leaflet_map.invalidateSize();
                        }
                    }
                }
            } catch (e) {
                // sandbox blocks iframe.contentDocument access — resize event is best effort
            }
        }

        // --- Deduplicate handle on rerun ---
        function removeOldHandle() {
            var sibling = iframe.nextElementSibling;
            if (sibling && sibling.dataset && sibling.dataset.resizeHandle === '1') {
                sibling.remove();
            }
        }
        removeOldHandle();

        // Restore persisted height on load
        applyHeight(loadHeight());
        iframe.dataset.foliumHandled = '1';

        // --- Create sibling handle UNDER the iframe in parent doc ---
        var handle = parentDoc.createElement('div');
        handle.title = 'Drag to resize map';
        handle.dataset.resizeHandle = '1';
        handle.style.cssText = [
            'height:10px',
            'background:#e8e8e8',
            'cursor:ns-resize',
            'border-radius:0 0 6px 6px',
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

        // Insert directly after iframe so it is a sibling, not inside map
        if (iframe.parentNode) {
            iframe.parentNode.insertBefore(handle, iframe.nextSibling);
        }

        // --- Drag state with rAF throttle; persist only on pointerup ---
        var dragStartY = null;
        var dragStartH = null;
        var pendingH = null;
        var rafId = null;
        var currentH = loadHeight();

        function flushHeight() {
            rafId = null;
            if (pendingH !== null) {
                currentH = pendingH;
                applyHeight(currentH);
                pendingH = null;
            }
        }

        function scheduleHeight(h) {
            pendingH = Math.min(Math.max(h, MIN_HEIGHT), MAX_HEIGHT);
            if (rafId === null) {
                rafId = parentWin.requestAnimationFrame(flushHeight);
            }
        }

        handle.addEventListener('pointerdown', function(e) {
            if (!iframe.isConnected) { handle.remove(); return; }
            e.preventDefault();
            // pointer capture ensures we keep receiving moves even outside handle/map
            try { handle.setPointerCapture(e.pointerId); } catch (err) {}
            dragStartY = e.clientY;
            dragStartH = iframe.offsetHeight;
            pendingH = null;
            if (rafId !== null) { parentWin.cancelAnimationFrame(rafId); rafId = null; }
            handle.style.background = '#d0d0d0';
        });

        handle.addEventListener('pointermove', function(e) {
            if (dragStartY === null) return;
            if (!iframe.isConnected) { handle.remove(); return; }
            var h = dragStartH + (e.clientY - dragStartY);
            scheduleHeight(h);
        });

        function endDrag(e) {
            if (dragStartY === null) return;
            // Flush any pending frame immediately so final height is exact
            if (rafId !== null) {
                parentWin.cancelAnimationFrame(rafId);
                rafId = null;
            }
            if (pendingH !== null) {
                currentH = pendingH;
                applyHeight(currentH);
                pendingH = null;
            } else if (e) {
                var h = dragStartH + (e.clientY - dragStartY);
                h = Math.min(Math.max(h, MIN_HEIGHT), MAX_HEIGHT);
                currentH = h;
                applyHeight(currentH);
            }
            // Persist once per drag-end, not per move
            saveHeight(currentH);
            try { handle.releasePointerCapture(e.pointerId); } catch (err) {}
            dragStartY = null;
            dragStartH = null;
            handle.style.background = '#e8e8e8';
        }

        handle.addEventListener('pointerup', endDrag);
        handle.addEventListener('pointercancel', function(e) {
            if (rafId !== null) { parentWin.cancelAnimationFrame(rafId); rafId = null; }
            pendingH = null;
            dragStartY = null;
            dragStartH = null;
            handle.style.background = '#e8e8e8';
        });
        // Also listen on parent window for pointerup outside handle (safety)
        parentDoc.addEventListener('pointerup', function(e) {
            if (dragStartY !== null) endDrag(e);
        });
    })();
    {% endmacro %}
    """)

    def __init__(self, map_key):
        super().__init__()
        self._name = 'DragHandlePlugin'
        self._map_key = map_key
