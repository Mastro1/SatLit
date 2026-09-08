"""
Sidebar module for SatLit.
Contains: Authentication status, Settings popup, Task monitor, Presets, History loader.
"""
import sys
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import ee
import streamlit as st

from src.domain.extractors.BaseExtractor import BaseExtractor
from src.infrastructure.persistence.HistoryManager import HistoryManager
from src.infrastructure.persistence.PresetManager import PresetManager
from src.infrastructure.persistence.PresetSerializer import (
    PresetValidationError,
    from_share_payload,
    preview_summary,
    sanitize_filename,
    to_share_json,
)
from src.interface.main_panel import snapshot_config_from_session

PROJECT_ROOT = Path(__file__).parent.parent.parent
USER_PREFS_FILE = PROJECT_ROOT / ".cache" / "user_preferences.json"
GEE_TASKS_URL = "https://code.earthengine.google.com/tasks"

CACHE_AGE_PRESETS = {
    "1 month": timedelta(days=30),
    "3 months": timedelta(days=90),
    "6 months": timedelta(days=180),
    "1 year": timedelta(days=365),
}


def render(settings_service):
    """Renders the sidebar components."""
    with st.sidebar:
        render_shortcut_prompt()
        render_update_banner()
        st.header("🎮 Control Center")
        
        # 1. Authentication Status
        render_auth_status(settings_service)
        
        st.divider()
        
        # 2. Settings + Presets (open as dialogs)
        render_settings_popup(settings_service)
        render_presets_popup()
        
        st.divider()
        
        # 3. Task Monitor
        render_task_monitor()

        st.divider()

        # 4. History
        render_history_loader()


def render_auth_status(settings_service):
    """Checks and displays GEE Auth status with proper initialization."""
    project_id = settings_service.get_setting("gee", "project_id", "my-project")
    
    # Use session state to track initialization status
    if 'gee_initialized' not in st.session_state:
        st.session_state.gee_initialized = False
        st.session_state.gee_error = None
    
    # Try to initialize if not already done
    if not st.session_state.gee_initialized:
        try:
            ee.Initialize(project=project_id)
            st.session_state.gee_initialized = True
            st.session_state.gee_error = None
        except ee.EEException as e:
            # Try to authenticate first
            try:
                ee.Authenticate()
                ee.Initialize(project=project_id)
                st.session_state.gee_initialized = True
                st.session_state.gee_error = None
            except Exception as auth_err:
                st.session_state.gee_initialized = False
                st.session_state.gee_error = str(auth_err)
        except Exception as e:
            st.session_state.gee_initialized = False
            st.session_state.gee_error = str(e)
    
    # Display status
    if st.session_state.gee_initialized:
        st.success(f"🟢 GEE Connected: `{project_id}`")
    else:
        st.error("🔴 GEE Disconnected")
        if st.session_state.gee_error:
            st.caption(f"Error: {st.session_state.gee_error[:50]}...")
        
        if st.button("🔄 Reconnect", use_container_width=True):
            try:
                ee.Authenticate()
                ee.Initialize(project=project_id)
                st.session_state.gee_initialized = True
                st.session_state.gee_error = None
                st.rerun()
            except Exception as auth_err:
                st.session_state.gee_error = str(auth_err)
                st.error(f"Auth failed: {auth_err}")


@st.dialog("⚙️ Settings")
def settings_dialog(settings_service):
    """Settings popup modal dialog."""
    try:
        version = (Path(__file__).parent.parent.parent / "VERSION").read_text().strip()
    except Exception:
        version = "unknown"
    st.markdown(f"<p style='color:#888888; font-size:0.85rem; margin-top:-12px; margin-bottom:8px;'>v{version}</p>", unsafe_allow_html=True)

    st.markdown("### Google Earth Engine")
    
    # GEE Project ID
    current_project = settings_service.get_setting("gee", "project_id", "")
    new_project = st.text_input(
        "GEE Project ID",
        value=current_project,
        help="Your Google Cloud Project ID for GEE"
    )
    
    # Drive folder
    current_drive_folder = settings_service.get_setting("gee", "drive_folder", "GEE_Exports")
    new_drive_folder = st.text_input(
        "Google Drive Folder",
        value=current_drive_folder,
        help="Folder name in your Google Drive for exports"
    )
    
    st.markdown("### Local Paths")

    # Local download folder
    current_local_path = settings_service.get_setting("paths", "download_folder_local", "")
    new_local_path = st.text_input(
        "Local Download Folder",
        value=current_local_path,
        help="Where to save files for local downloads"
    )

    new_cache = _render_cache_section(settings_service)

    st.markdown("### Defaults")

    # Default reducer
    reducers = ['mean', 'sum', 'max', 'min', 'median']
    current_reducer = settings_service.get_setting("defaults", "default_reducer", "mean")
    new_reducer = st.selectbox(
        "Default Reducer",
        options=reducers,
        index=reducers.index(current_reducer) if current_reducer in reducers else 0
    )

    _render_desktop_shortcut_controls()

    # Save button
    col1, col2 = st.columns(2)
    with col1:
        if st.button("💾 Save", use_container_width=True, type="primary"):
            # Update all settings
            settings_service.update_setting("gee", "project_id", new_project)
            settings_service.update_setting("gee", "drive_folder", new_drive_folder)
            settings_service.update_setting("paths", "download_folder_local", new_local_path)
            settings_service.update_setting("paths", "cache_folder", new_cache)
            settings_service.update_setting("defaults", "default_reducer", new_reducer)

            # Force re-initialization if project changed
            if new_project != current_project:
                st.session_state.gee_initialized = False

            st.success("✅ Settings saved!")
            st.rerun()

    with col2:
        if st.button("❌ Cancel", use_container_width=True):
            st.rerun()


def _format_bytes(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"


def _render_cache_section(settings_service) -> str:
    """Renders the Cache settings section (path + history clear). Returns the path value."""
    st.markdown("### Cache")

    current_cache = settings_service.get_setting("paths", "cache_folder", "./.cache/")
    new_cache = st.text_input(
        "Cache Folder",
        value=current_cache,
        help="Where to store job history",
    )

    history_manager = HistoryManager()
    stats = history_manager.get_cache_stats()

    st.caption(
        f"**{_format_bytes(stats['file_size_bytes'])}** · "
        f"{stats['entry_count']} entr{'y' if stats['entry_count'] == 1 else 'ies'}"
    )
    if stats["oldest_timestamp"] and stats["newest_timestamp"]:
        oldest = stats["oldest_timestamp"][:10]
        newest = stats["newest_timestamp"][:10]
        st.caption(f"{oldest} → {newest}")

    clear_mode = st.radio(
        "Clear mode",
        options=["Clear everything", "Clear older than…"],
        horizontal=True,
        key="settings_cache_clear_mode",
    )

    age_label = None
    if clear_mode == "Clear older than…":
        age_label = st.selectbox(
            "Remove entries older than",
            options=list(CACHE_AGE_PRESETS.keys()),
            index=list(CACHE_AGE_PRESETS.keys()).index("6 months"),
            key="settings_cache_age_preset",
        )

    confirmed = st.checkbox(
        "I understand this cannot be undone",
        key="settings_cache_confirm",
    )
    if st.button(
        "Clear cache",
        use_container_width=True,
        disabled=not confirmed,
        key="settings_cache_clear_btn",
    ):
        if clear_mode == "Clear everything":
            removed = history_manager.clear_all()
        else:
            cutoff = datetime.now() - CACHE_AGE_PRESETS[age_label]
            removed = history_manager.clear_older_than(cutoff)
        st.success(f"Removed {removed} histor{'y entry' if removed == 1 else 'y entries'}.")
        st.rerun()

    return new_cache


def _render_desktop_shortcut_controls():
    """Creates or updates the desktop shortcut via install.py."""
    st.markdown("### Desktop Shortcut")
    st.caption("Creates or updates the GEE-UI shortcut on your desktop.")
    if st.button(
        "Create / update desktop shortcut",
        use_container_width=True,
        key="settings_create_shortcut",
    ):
        install_script = PROJECT_ROOT / "install.py"
        try:
            result = subprocess.run(
                [sys.executable, str(install_script), "--create-shortcut-only"],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                _save_pref("shortcut_created", True)
                st.success("Desktop shortcut created or updated.")
            else:
                detail = (result.stderr or result.stdout or "Unknown error").strip()
                st.error(f"Could not create shortcut: {detail[:200]}")
        except Exception as e:
            st.error(f"Could not create shortcut: {e}")


def render_settings_popup(settings_service):
    """Settings button that opens the dialog."""
    if st.button("⚙️ Settings", use_container_width=True):
        settings_dialog(settings_service)


def render_task_monitor():
    """Displays recent GEE tasks."""
    st.subheader("📋 Tasks")
    st.markdown(f"[Open in Google Earth Engine]({GEE_TASKS_URL})")

    if st.button("🔄 Refresh Tasks", use_container_width=True):
        try:
            tasks = BaseExtractor.monitor_tasks(limit=5)
            if tasks:
                for task in tasks:
                    state = task.get('state', 'UNKNOWN')
                    if state == 'COMPLETED':
                        icon = "🟢"
                    elif state == 'RUNNING':
                        icon = "🔵"
                    elif state == 'FAILED':
                        icon = "🔴"
                    elif state == 'READY':
                        icon = "⚪"
                    else:
                        icon = "⚪"
                    
                    desc = task.get('description', 'No Description')[:25]
                    st.text(f"{icon} {desc} [{state}]")
            else:
                st.info("No recent tasks found.")
        except Exception as e:
            st.warning(f"Could not fetch tasks: {str(e)[:50]}")


def render_presets_popup():
    """Presets button that opens the dialog."""
    if st.button("📌 Presets", use_container_width=True, key="btn_open_presets"):
        presets_dialog()


def _sync_preset_selectbox(names: list, labels: dict) -> None:
    """Restore selectbox only when its value is missing/invalid (e.g. dialog reopened)."""
    current = st.session_state.get("preset_dialog_select")
    if current in names:
        return
    remembered_id = st.session_state.get("preset_selected_id")
    remembered_name = next(
        (n for n in names if labels[n].get("preset_id") == remembered_id),
        None,
    )
    st.session_state["preset_dialog_select"] = remembered_name or names[0]


@st.dialog("📌 Presets")
def presets_dialog():
    """Presets popup: load, save, import, and share."""
    st.caption(
        "Save layouts you reuse often. Share them with teammates via file or clipboard — "
        "no credentials or local file paths are included."
    )

    manager = PresetManager()
    presets = manager.list_presets()
    selected = None

    # Deferred reset for the Load-section widget keys. Widget state may only
    # be touched BEFORE its widget is created in a run, so handlers below
    # set this flag + rerun instead of mutating the keys directly
    # (same _pending_* pattern as the file-import path).
    if st.session_state.pop("_reset_preset_select_ui", False):
        st.session_state.pop("preset_dialog_select", None)
        st.session_state["preset_confirm_delete"] = False

    with st.expander("Load", expanded=True):
        st.caption("Pick a saved preset and apply it to the form.")
        if presets:
            labels = {p["name"]: p for p in presets}
            names = list(labels.keys())
            _sync_preset_selectbox(names, labels)
            selected_name = st.selectbox(
                "My presets",
                options=names,
                key="preset_dialog_select",
                label_visibility="collapsed",
            )
            selected = labels[selected_name]
            st.session_state["preset_selected_id"] = selected.get("preset_id")

            notes = (selected.get("notes") or "").strip()
            if notes:
                st.caption(notes)

            confirm_delete = st.checkbox(
                "Confirm before deleting",
                key="preset_confirm_delete",
                help="Required to enable Delete. This cannot be undone.",
            )
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Load", use_container_width=True, type="primary", key="btn_preset_load"):
                    st.session_state["preset_selected_id"] = selected.get("preset_id")
                    st.session_state["loaded_settings"] = dict(selected.get("config") or {})
                    st.rerun()
            with c2:
                if st.button(
                    "Delete",
                    use_container_width=True,
                    disabled=not confirm_delete,
                    key="btn_preset_delete",
                ):
                    deleted_id = selected.get("preset_id")
                    manager.delete(deleted_id)
                    if st.session_state.get("preset_selected_id") == deleted_id:
                        st.session_state.pop("preset_selected_id", None)
                    st.session_state["_reset_preset_select_ui"] = True
                    st.rerun()
        else:
            st.caption("No presets yet — open **Save** below to create one from the form.")

    with st.expander("Save", expanded=False):
        st.caption("Store the current form as a reusable preset.")
        try:
            config = snapshot_config_from_session()
        except Exception as exc:
            st.error(f"Could not read the current form: {exc}")
            config = None

        if config and not config.get("satellite"):
            st.caption("Pick a satellite in the form before saving a preset.")
        elif config:
            default_name = PresetManager.suggest_name_from_entry(config)
            if st.session_state.get("save_current_preset_name_seed") != default_name:
                st.session_state["save_current_preset_name"] = default_name
                st.session_state["save_current_preset_name_seed"] = default_name
            name = st.text_input("Name", key="save_current_preset_name")
            notes = st.text_area("Notes (optional)", value="", key="save_current_preset_notes", height=68)
            if config.get("geometry_source") == "Shapefile" or config.get("uploaded_shapefile"):
                st.caption(
                    "Shapefile paths stay on this machine. Shared copies will ask teammates to pick their own file."
                )
            if st.button("Save as preset", type="primary", use_container_width=True, key="btn_confirm_save_current"):
                try:
                    preset = manager.save_from_session_config(config, name=name, notes=notes)
                    st.session_state["preset_selected_id"] = preset.get("preset_id")
                    st.session_state["_reset_preset_select_ui"] = True
                    st.success(f'Preset "{preset["name"]}" saved.')
                    st.rerun()
                except PresetValidationError as exc:
                    st.error(str(exc))

    with st.expander("Import", expanded=False):
        st.caption("Bring in a preset from a teammate (paste or upload JSON).")
        _render_import_preset(manager)

    with st.expander("Share", expanded=False):
        st.caption("Export the preset selected under Load.")
        if selected:
            payload = to_share_json(selected)
            preset_id = selected.get("preset_id") or selected.get("name")
            filename = f"{sanitize_filename(selected.get('name', 'preset'))}.json"
            st.download_button(
                "Download JSON",
                data=payload.encode("utf-8"),
                file_name=filename,
                mime="application/json",
                use_container_width=True,
                key=f"btn_download_preset_json_{preset_id}",
            )
            st.code(payload, language="json")
        else:
            st.caption("Select a preset under **Load** first.")


def _clear_import_draft():
    st.session_state.pop("preset_import_raw", None)
    st.session_state.pop("preset_import_error", None)
    st.session_state.pop("preset_import_name", None)


def _render_import_preset(manager: PresetManager):
    method = st.radio(
        "Import method",
        options=["Paste JSON", "Upload file"],
        horizontal=True,
        key="preset_import_method",
    )

    if method == "Paste JSON":
        pasted = st.text_area(
            "Paste preset JSON",
            height=140,
            key="preset_paste_text",
            placeholder='{"schema_version": 1, "type": "gee_extraction_preset", ...}',
        )
        if st.button("Import paste", use_container_width=True, key="btn_parse_preset_paste"):
            text = (pasted or "").strip()
            if not text:
                st.session_state["preset_import_error"] = "Paste the full preset JSON first, then click Import paste."
                st.session_state.pop("preset_import_raw", None)
            else:
                st.session_state["preset_import_raw"] = text
                st.session_state.pop("preset_import_error", None)
                st.session_state.pop("preset_import_name", None)
    else:
        uploaded = st.file_uploader(
            "Preset file",
            type=["json"],
            key="preset_file_uploader",
        )
        if st.button("Import file", use_container_width=True, key="btn_parse_preset_file"):
            if uploaded is None:
                st.session_state["preset_import_error"] = "Choose a .json file first, then click Import file."
                st.session_state.pop("preset_import_raw", None)
            else:
                st.session_state["preset_import_raw"] = uploaded.getvalue()
                st.session_state.pop("preset_import_error", None)
                st.session_state.pop("preset_import_name", None)

    if st.session_state.get("preset_import_error"):
        st.error(st.session_state["preset_import_error"])

    raw = st.session_state.get("preset_import_raw")
    if raw is None:
        return

    try:
        draft = from_share_payload(raw)
    except PresetValidationError as exc:
        st.error(str(exc))
        if st.button("Clear", key="btn_clear_bad_import"):
            _clear_import_draft()
            st.rerun()
        return

    summary = preview_summary(draft["name"], draft["config"])
    st.markdown("**Preview**")
    st.write(
        f"**{summary['name']}** · `{summary['satellite']}` · "
        f"{summary['bands_count']} band(s) · {summary['geometry_source']} · {summary['date_range']}"
    )
    for warning in draft.get("warnings") or []:
        st.warning(warning)

    if "preset_import_name" not in st.session_state:
        st.session_state["preset_import_name"] = manager.unique_name(draft["name"])

    import_name = st.text_input(
        "Name in My Presets",
        key="preset_import_name",
    )

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Add & load", type="primary", use_container_width=True, key="btn_import_add_load"):
            try:
                preset = manager.add(
                    name=import_name,
                    config=draft["config"],
                    notes=draft.get("notes") or "",
                )
                _clear_import_draft()
                st.session_state["preset_selected_id"] = preset.get("preset_id")
                st.session_state["_reset_preset_select_ui"] = True
                st.session_state["loaded_settings"] = dict(preset["config"])
                st.success(
                    f'Preset "{preset["name"]}" is ready — check the form and run when you like.'
                )
                st.rerun()
            except PresetValidationError as exc:
                st.error(str(exc))
    with c2:
        if st.button("Add to My Presets", use_container_width=True, key="btn_import_add_only"):
            try:
                preset = manager.add(
                    name=import_name,
                    config=draft["config"],
                    notes=draft.get("notes") or "",
                )
                _clear_import_draft()
                st.session_state["preset_selected_id"] = preset.get("preset_id")
                st.session_state["_reset_preset_select_ui"] = True
                st.success(f'Preset "{preset["name"]}" added to My Presets.')
                st.rerun()
            except PresetValidationError as exc:
                st.error(str(exc))

    if st.button("Load now (don't save)", use_container_width=True, key="btn_import_load_only"):
        _clear_import_draft()
        st.session_state["loaded_settings"] = dict(draft["config"])
        st.rerun()


def render_history_loader():
    """Loads previous run configurations."""
    st.subheader("📜 History")
    history_manager = HistoryManager()
    history = history_manager.get_history()

    if not history:
        st.caption("No job history available.")
        return

    options = {f"{h['timestamp'][:16]} - {h['satellite'][:15]}": h for h in history}
    selected_option = st.selectbox(
        "Load Previous Run",
        options=list(options.keys()),
        label_visibility="collapsed",
    )
    if st.button("📥 Load Settings", use_container_width=True, key="btn_history_load"):
        st.session_state["loaded_settings"] = options[selected_option]
        st.success("Settings loaded!")
        st.rerun()


def _load_prefs() -> dict:
    if USER_PREFS_FILE.exists():
        try:
            return json.loads(USER_PREFS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_pref(key: str, value) -> None:
    prefs = _load_prefs()
    prefs[key] = value
    USER_PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    USER_PREFS_FILE.write_text(json.dumps(prefs, indent=2), encoding="utf-8")


def render_shortcut_prompt():
    """One-time prompt to create a desktop shortcut. Never shown again after answered."""
    if _load_prefs().get("shortcut_created") is not None:
        return
    st.info("Create a desktop shortcut for GEE-UI?", icon="🖥️")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Yes please", use_container_width=True, type="primary", key="btn_shortcut_yes"):
            install_script = PROJECT_ROOT / "install.py"
            subprocess.Popen([sys.executable, str(install_script), "--create-shortcut-only"])
            _save_pref("shortcut_created", True)
            st.success("Shortcut created!")
            st.rerun()
    with col2:
        if st.button("No thanks", use_container_width=True, key="btn_shortcut_no"):
            _save_pref("shortcut_created", False)
            st.rerun()
    st.divider()


def render_update_banner():
    """Renders an update notification banner if a newer version is available."""
    update_info = st.session_state.get('update_info')
    if update_info is None:
        return

    if update_info.error == "git_not_found":
        st.warning(
            "**Auto-update unavailable** — Git was not found on your system.  \n"
            "To receive future updates, please install Git from [git-scm.com](https://git-scm.com) "
            "or re-download the application directly from GitHub.",
            icon="⚠️",
        )
        st.divider()
        return

    if not update_info.update_available:
        return
    if st.session_state.get('update_dismissed'):
        return

    st.info(
        f"**Update available:** v{update_info.remote_version}  \n"
        f"You are on v{update_info.current_version}",
        icon="🆕",
    )
    if update_info.has_local_changes:
        st.warning(
            "Uncommitted local changes detected. Run `git stash` in your terminal before updating.",
            icon="⚠️",
        )
    if st.button("Dismiss", use_container_width=True, key="btn_dismiss"):
        st.session_state['update_dismissed'] = True
        st.rerun()
    if st.button("Update & Restart", type="primary", use_container_width=True, key="btn_update"):
        _do_update()
    st.divider()


def _do_update():
    """Pulls latest changes, reinstalls requirements, and restarts the app."""
    import time
    checker = st.session_state.get('update_checker')
    if not checker:
        st.error("Update service unavailable. Please restart the app manually.")
        return
    with st.spinner("Pulling latest changes..."):
        success, message = checker.perform_update()
    if success:
        st.success(message)
        time.sleep(1.5)
        checker.restart_app()
    else:
        st.error(message)
        st.session_state.pop('update_info', None)
