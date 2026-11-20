#!/bin/bash
# Double-click this file (after "Pull origin" in GitHub Desktop) to rebuild the Nockd app with the newest page.
# Then press the play button in Xcode, or archive for TestFlight.
cd "$(dirname "$0")/.." || exit 1
echo "Building the Nockd page for the app..."
python3 -m scripts.build_app || { echo; echo "Build failed. Send a screenshot of this window."; read -r -p "Press Enter to close."; exit 1; }
cd ios-app || exit 1
[ -d node_modules ] || npm install
echo "Copying it into the iPhone project..."
npx cap sync ios || { echo; echo "Sync failed. Send a screenshot of this window."; read -r -p "Press Enter to close."; exit 1; }
echo
echo "Done. In Xcode, press the play button to put the new version on your phone."
read -r -p "Press Enter to close."
