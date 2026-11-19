# Nockd iPhone app

This folder is the iPhone app. It is a Capacitor 8 app: a small native iOS app that shows the Nockd page.

- **Inside the app:** the page itself, about 0.8 MB. That includes the page, Leaflet and Chart.js, the logo and the icon.
- **Loaded from the website at runtime:** all forecast, tide and current data. It is about 250 MB and refreshed every
  few hours by the GitHub workflow.

Because the data comes from the website, the app shows fresh data without App Store updates. You only send a new app
version when the page itself changes.

## What you need

- **A Mac with Xcode,** from the Mac App Store. It must be a version that supports Capacitor 8; check
  capacitorjs.com/docs/ios for the current minimum.
- **Node.js** (nodejs.org, the LTS version) and **git.**
- **An Apple Developer Program membership,** for TestFlight and the App Store. Check developer.apple.com for the
  current price; it has been $99 a year in the US.
- **The website's real Netlify address,** for example `https://something.netlify.app/`.
  - A domain forwarded *with masking* does not work here. Masking puts the site inside a frame, and the app cannot read
    data files through that.
  - A domain pointed straight at Netlify with DNS also works.

## One-time setup on the Mac

Run these in Terminal.

```bash
git clone https://github.com/njpboatworks-hub/TIde.git
cd TIde/ios-app
npm install
cd ..
python3 -m scripts.build_app
cd ios-app
npx cap sync ios
npx cap open ios
```

The app loads its data from `https://nockd.netlify.app/` (saved in `ios-app/data-host.txt`). To change it, run
`python3 -m scripts.build_app --data https://NEW-ADDRESS/` once.

## In Xcode

1. Click **App** at the top of the file list, then the **Signing & Capabilities** tab.
2. Tick **Automatically manage signing** and choose your team, which is your Apple Developer account.
3. The **Bundle Identifier** is `com.njpboatworks.nockd`.
   - You can change it in `capacitor.config.json` before the first upload.
   - It cannot be changed after the app is on the App Store.
4. To try the app, plug in your iPhone (or pick a simulator) and press the ▶ button.
5. To upload it:
   - Choose **Product → Archive**, then **Distribute App → App Store Connect**.
   - Install it on your phone from **TestFlight** first.
   - Then submit it for review in App Store Connect.

## Updating the app later

Run these from the `TIde` folder.

```bash
git pull
python3 -m scripts.build_app
cd ios-app
npx cap sync ios
```

Then archive and upload again. In Xcode, raise the **Build** number on the General tab first.

## Things App Store Connect will ask for

- **Privacy policy URL:** `https://nockd.netlify.app/privacy.html`.
- **App Privacy questions:**
  - Nockd has no accounts, analytics or ads.
  - Location is used only on the phone, when you tap "Use my location".
  - AI coach questions are sent to the server and to Anthropic to get an answer.
  - Answer from what is true at the time you submit.
- **Screenshots:** take them from the iPhone simulator. App Store Connect lists the screen sizes it needs.
- **Age rating and category:** Weather or Sports fits. Do not choose the Kids category, because that has extra
  rules.

## Known risks

- **Apple guideline 4.2 (minimum functionality).** Apple can reject apps that are "just a website".
  - What helps Nockd: it is an interactive map tool, it uses location, and the page is built into the app.
  - It is still a judgement call by the reviewer, and an approval is not guaranteed.
- **Location prompts.** The first time someone taps "Use my location", iOS asks for permission using the text in
  `ios/App/App/Info.plist`. Some iOS versions may also ask a second time for the page; this has not been tested on a
  real iPhone yet.
- **Netlify bandwidth.** Every app user downloads map data from Netlify, the same as a website visitor, so heavy use
  counts toward your Netlify plan.
