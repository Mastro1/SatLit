import streamlit as st
import sys
from pathlib import Path

# Add project root to path
root_path = Path(__file__).parent.parent.parent
sys.path.append(str(root_path))
from src.infrastructure.configuration.SettingsService import SettingsService
from src.infrastructure.update.UpdateChecker import UpdateChecker
from src.interface import sidebar, main_panel
# Page Configuration
st.set_page_config(
    page_title="SatLit",
    page_icon="assets/favicon.png",
    layout="wide",
    initial_sidebar_state="expanded",
)

def main():
    """Main application loop."""
    
    # Initialize Services
    settings_service = SettingsService()

    # Check for updates once per session (not on every Streamlit rerun)
    if 'update_info' not in st.session_state:
        checker = UpdateChecker()
        st.session_state['update_info'] = checker.check_for_updates()
        st.session_state['update_checker'] = checker

    # Render Interface
    sidebar.render(settings_service)
    main_panel.render(settings_service)

if __name__ == "__main__":
    main()
