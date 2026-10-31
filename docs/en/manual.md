# EVBro manual: every screen and what it does

*Versión en español: [docs/manual.md](../manual.md).*

EVBro is your Ebro car's companion: it shows what the car tells the Chery cloud, sends it commands, keeps its history (trips, charges, fuel-ups, garage visits, expenses) and alerts you about what matters. It is designed for the phone (bottom dock with **Home, Energy, Comfort and More**) and on a computer it turns into a sidebar with every section in view. Available in Spanish, English and Portuguese (Settings → System → Language).

One rule holds across the whole app: **what you see comes from the car**. If a value has not arrived, you see "No data", never a made-up value or a default "closed".

## Home

The front page of the selected car (the car selector is at the top if you have several).

- **Header with the photo** of the car, its name, number plate and state: *Asleep*, *Driving*, *Charging*… The coloured dot says whether the data is recent.
- **Battery and petrol**: percentage and range of each (a pure EV has no petrol; a combustion car has no battery).
- **Car figure** seen from above: open doors and tailgate swing outwards, open windows and sunroof are painted amber, the padlock says whether it is locked, the plug appears with the cable connected and each wheel carries its pressure. Below, one line sums up what is open. **Tapping a part opens its action** (windows, boot, sunroof, lock).
- **Action tiles**: Lock, Find (lights and horn), Boot, Windows, Sunroof, Locate GPS and Alarm. The title says what it is and the state only *Open/Closed*. Whatever **opens** the car asks for the Ebro PIN on every send (configurable, see Settings → Account).
- **Map**: the last known position. Folded, it is a fixed preview; a tap expands it full screen. **Centre** goes back to the car and **Go to** opens Google Maps to it. The position comes from the cloud (it does not wake the car); **Locate GPS** does ask the car for a fresh fix.
- **Tyres (TPMS)**: pressure and temperature of each wheel.
- **Refresh** button (top right): queries the cloud now. It does not wake the car.

## Energy

Everything about the battery and charging.

- **Current charge** and **battery target** (50–100 %, in steps of 5, with the slider). The target is kept in EVBro, not in the car: when it is reached while charging, EVBro **pauses** the charge and resumes it on its own when plugged in with the battery 5 points below.
- **Charge control: Stop / Start**. The car does not allow stopping or starting a charge directly; EVBro does it through the car's own **scheduled charging** (it schedules a window that already ended to stop, and removes it to start). A "How it works" panel explains it on the screen itself.
- **Scheduled charging**: start and end time (or duration) to charge every day in that window (off-peak tariff). It is written to the car.
- **EV consumption · 30 days**: kWh/100 km of the last month's trips.
- **Battery data**: power, voltage, current and time left while charging.
- **Charge history** (tabs *Session curve* and *History*): each session with place, kWh into the battery (estimated from the battery points gained) and, if you enter them, the kWh from your meter, the real **efficiency** and the cost. The cost comes from the **tariff of the geofence** where you charged (fixed, free, 2.0TD or PVPC) or you type it with **Edit cost**. A charge can be marked AC or DC fast and its power curve viewed minute by minute.

An honest note: charge control through scheduling is built following the official app's protocol, but **it could not be validated in every situation against the real car**. If something does not add up, report it.

## Comfort

What can be switched on from the sofa.

- **Climate control**: on/off with set temperature (16–30 °C) and presets *Cool / 22 °C / Warm*. Turning it on sends the chosen temperature.
- **Glass and steering wheel**: windscreen and rear window defrost, heated steering wheel.
- **Seats**: heating and ventilation per seat.
- **Scheduled climate**: the server turns the climate control on at the time and days you choose, even with the app closed. It needs the PIN saved in the account.
- Every button shows the car's real state; after a command it shows "waiting for confirmation" until the car confirms.

## More

The menu with the remaining sections, grouped as **History** and **Manage**.

### History

**Statistics.** Charts of the history with a period picker (12 months, this year, last year, all…): kilometres per month, energy per month (kWh and litres), spending per month, cost per kilometre, electric consumption per month, battery health (usable capacity worked out from each large charge), consumption by speed, where it charges and drain while parked (battery the car loses while parked).

**Timeline.** Everything recorded about the car in one list by date (trips, charges, fuel-ups, garage, expenses, documents, geofence arrivals and departures), with filters by type. At the top, the **global search**: a part number, a garage, an invoice or an address, across all your cars. Tapping an item takes you to its screen.

**Trips.** Each detected drive with origin and destination (address), odometer kilometres, duration, battery and petrol used and speeds. **See route** opens the map with the track, which can be **replayed** and downloaded as **GPX**. Filters by geofence, place, minimum distance, speed and period. **Frequent places**: where the car ends up most often; a geofence can be created from there. **Journeys**: groups, between two dates, the drives, charges, fuel-ups and expenses of a holiday and tells you what it all cost and used. Trips are detected on their own from the car's data; incomplete ones (no end) can be corrected by hand.

**Fuel-ups.** Real petrol consumption: litres between fill-ups, cost per 100 km, price per litre, range on a full tank. Fuel-ups are detected on their own when the tank level rises (with estimated litres); add any missing ones and enter the receipt price. Tick **Full tank** when you fill up: between two fill-ups the consumption is exact.

**Maintenance.** Two plans: the **official EBRO** one (the maker's schedule: 15,000 to 90,000 km, with what is replaced and inspected at each service and the official workshop time) or a **custom** one with your own tasks and intervals in kilometres or months. The **ITV** (roadworthiness test) is worked out from the registration date (or from the sticker you enter). Record each **service**, **one-off job** (pads, 12 V battery…) or ITV with date, kilometres, garage, cost, notes, the **parts fitted** and the **invoice** as PDF or photo. Maintenance alerts go out through the channels in Settings → Alerts.

**Expenses.** Insurance, taxes, garage, tolls, fines and other payments, by category. For recurring ones (insurance, tax) you set the **next time** and EVBro reminds you 30 days before.

**Costs.** The balance of everything: purchase, charges, fuel-ups, maintenance and expenses; total since purchase, running cost, cost per 100 km and by category, with a monthly chart and the period picker.

### Manage

**Cars.** The list of your cars (tap one and the whole app switches to it) and the **sheet** of the selected one:
- *Details*: name, make, model, version, colour, environmental badge, number plate, chassis number, registration and delivery dates, purchase price and kilometres, **insurance** (company, policy, cover, assistance phone, renewal), **documents** (registration certificate, technical sheet, policy, purchase invoice…: uploaded and opened from the app), **tyres** (sizes and reference pressures for the alert) and **energy** (usable battery, tank and engine type; changing the battery recalculates every trip and charge).
- *Consumables*: what your car is fitted with (oil, filters, pads, tyres, wiper blades…), with part number and make, so you know it straight away at the garage. It updates on its own when you record a job with parts. You can **propose** a part number to the shared catalogue, which the administrator reviews before publishing it to the other owners of the same model.
- **Car book (PDF)**: a document with the sections you choose (sheet, maintenance, trips, charges, fuel-ups, expenses, geofences, documents) to keep, print or hand over with the car. It comes out in your language.
- **Add a car without an Ebro account**: a car of any make to keep its maintenance, expenses, costs and parts (no live data). With a GPS tracker and a Traccar server it also gets position and trips.
- **Photo** of the car, used in the Home header.

**Geofences.** Areas on the map (home, work, charger…) with a radius. They serve three purposes: **alerting** on arrival or departure, **pricing** the charges made inside (fixed tariff, free, 2.0TD with its three prices or regulated PVPC) and classifying trips. The **Arrivals and departures** card is the log of every pass through them.

**Settings.**
- *Account*: your EVBro user (change password, sign out), **fingerprint sign-in** (add this device and sign in without a password; requires HTTPS), the **Ebro account** (connect, sync cars, save or change the PIN) and **when to ask for the PIN**: only when opening the car (default), always or never. **Your data**: download everything as a ZIP and import a ZIP on another server. A link to what data this server keeps. Connection to Home Assistant's **MQTT** broker so your cars appear there.
- *Sharing*: linked accounts (partner, family) and what each can do with each car: view; view and edit; view and control; view, edit and control.
- *Alerts*: notifications on this device (Web Push), Telegram, Pushover or webhook, and which events alert: geofence arrival and departure, fuel reserve, faults, windows left open, low battery, charge completed or interrupted, maintenance, alarm, tyre pressure and due dates, with their thresholds. History of sent alerts.
- *Theme*: light, dark or automatic and accent colour (blue, pink, violet, green, amber, red). Saved on the device.
- *System*: GPS position through **Traccar**, **car channel** (the live connection to the cloud: status, messages received, Reconnect button), **language** of the account and installed version.

**Sensors and cables.** All the data the car sends, as it arrives, with name and explanation, by category (cables and charging, battery, tyres, body, climate, engine and diagnostics) and a search box. Useful to understand what each signal means.

**Alert centre** (bell in the header): the latest events and alerts of the car.

## How the data arrives

- The **poller** queries the Chery cloud every hour with the car parked, every 30 minutes plugged in, every 2 while charging and every minute while driving. Querying **does not wake the car**; **commands** do.
- The **car channel** (MQTT) receives doors, lock, cable and engine instantly, and confirms every command. It only works with the owner's account, and the cloud keeps **one live connection per account**: if you open the official app, the last one in keeps it. If the channel stops, Settings → System → Reconnect.
- Trips and charges are **detected on their own** from that data; the kWh of trips and charges are estimates (battery points × sheet capacity). The litres of a detected fuel-up are too, until you enter the ones on the receipt.

## The admin console

It is another web app, on another port, for the local network only. The administrator has no cars and sees nobody's data: it creates **managers**, issues **invitations**, sets **limits** (cars, linked accounts, file space), tunes the **server** (how much history is kept, polling intervals), moderates the shared consumables **catalogue** (approve, reject, edit proposals; connect to another server's catalogue) and reads a **log** of its actions and aggregate server figures. It is also in the three languages.

## What EVBro does not do (on purpose)

- It does not start the engine remotely (the car refuses) nor "really" stop or start a charge: it uses the schedule for that.
- It does not keep your PIN in the browser; on the server it is encrypted and only if you want.
- It does not send your data to anyone: the server is yours. The only outbound traffic goes to the Chery cloud (to talk to the car), OpenStreetMap tiles and the alert channels you enable.
