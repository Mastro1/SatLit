import sys
import os
from streamlit.web import cli as stcli
from pathlib import Path

def main():
    # Resolve the path to the streamlit app
    app_path = Path(__file__).parent / "src" / "interface" / "app.py"
    
    # Check if the file exists
    if not app_path.exists():
        print(f"Error: Could not find Streamlit app at {app_path}")
        sys.exit(1)

    # Set up sys.argv to mimic "streamlit run src/interface/app.py [args]"
    sys.argv = ["streamlit", "run", str(app_path)] + sys.argv[1:]
    
    # Execute streamlit
    import threading
    import time

    def print_delayed_message():
        time.sleep(1.5)
        print("\nPress Ctrl+C to exit")

    threading.Thread(target=print_delayed_message, daemon=True).start()
    sys.exit(stcli.main())

if __name__ == "__main__":
    main()
