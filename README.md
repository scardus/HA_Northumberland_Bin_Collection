# Northumberland Bin Collection — Home Assistant Integration

A custom Home Assistant integration that fetches bin collection schedules from [Northumberland County Council](https://bincollection.northumberland.gov.uk) and creates a calendar entity containing all collection events, allowing you to build automations that trigger on collection day and identify the bin type from the event.

---

## What It Does

After a simple postcode and address setup, the integration creates a single calendar entity in Home Assistant containing all bin collection events for the year:

| Entity | Contents |
|---|---|
| `calendar.bin_collection_<address>` | All collection events (General waste, Recycling, Garden waste) |

Each event's summary contains the bin type (e.g. `General waste`, `Recycling`, `Garden waste`), which your automations can read from `trigger.calendar_event.summary`.

The entity:
- Shows `on` all day on any collection day, `off` on all other days
- Appears in the Home Assistant Calendar panel with all three bin types visible
- The `event` attribute always reflects the next upcoming collection of any type

The schedule is refreshed automatically once a week, at a randomly chosen time that varies per installation to avoid overloading the council website.

---

## Requirements

- Home Assistant 2024.1.0 or later
- Your property must be within Northumberland County Council's area
- Internet access from your Home Assistant instance

---

## Installation

### Manual

1. Download or clone this repository
2. Copy the `custom_components/northumberland_bin_collection/` folder into your Home Assistant configuration directory:
   ```
   config/
   └── custom_components/
       └── northumberland_bin_collection/
           ├── __init__.py
           ├── api.py
           ├── calendar.py
           ├── config_flow.py
           ├── const.py
           ├── coordinator.py
           ├── manifest.json
           ├── strings.json
           └── translations/
               └── en.json
   ```
3. Restart Home Assistant

### HACS

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=scardus&repository=HA_Northumberland_Bin_Collection&category=integration)

1. Open HACS in your Home Assistant instance
2. Click the three-dot menu (⋮) in the top right corner and select **Custom repositories**
3. Enter the repository URL and set the category to **Integration**, then click **Add**:
   ```
   https://github.com/scardus/HA_Northumberland_Bin_Collection
   ```
4. Search for **Northumberland Bin Collection** in HACS and click **Download**
5. Restart Home Assistant

---

## Configuration

1. Go to **Settings → Integrations → Add Integration**
2. Search for **Northumberland Bin Collection**
3. Enter your postcode and press **Submit** — the integration will look up available addresses
4. Select your property from the dropdown list
5. Press **Submit** to complete setup

The integration will immediately fetch the full year's collection schedule and create your calendar entity.

> **Note:** If you have more than one property to monitor, repeat the process — each address gets its own calendar entity.

---

## Entities

### State

| State | Meaning |
|---|---|
| `on` | Today is a collection day (any bin type) |
| `off` | No collection today |

### Attributes

The calendar entity exposes an `event` attribute containing details of the next (or current) upcoming collection:

- `summary` — the bin type (e.g. `General waste`, `Recycling`, `Garden waste`)
- `start` — the collection date
- `end` — the day after the collection date (standard all-day event format)

In automation triggers, the bin type is available as `trigger.calendar_event.summary`.

---

## Example Files

The [`yaml/`](yaml/) folder in this repository contains ready-to-use automation examples. Download a file and import it into Home Assistant via **Settings → Automations → Import**, or copy the YAML directly into your automations configuration.

| File | Description |
|---|---|
| [`bin_day_notification.yaml`](yaml/bin_day_notification.yaml) | Uses a Frigate state classification to detect if a bin can be seen.  Sends a push notification at 7 pm, 8 pm and 9 pm the evening before collection, and again at 7:30 am on the day if no bin can be found. Includes the bin type in the message. |

---

## Example Automations

### Evening reminder the night before

Uses a calendar trigger with an offset so it fires at 8 pm the evening before each collection. The bin type is included in the message automatically.

```yaml
automation:
  - alias: "Bin collection eve reminder"
    triggers:
      - trigger: calendar
        event: start
        entity_id: calendar.bin_collection_your_address
        offset: "-04:00:00"
    actions:
      - action: notify.mobile_app_your_phone
        data:
          title: "Bin collection tomorrow"
          message: >
            Put the {{ trigger.calendar_event.summary | lower }}
            bin out tonight.
```

---

### Morning reminder on collection day

Sends a notification at 7 am on any collection morning, naming the bin type.

```yaml
automation:
  - alias: "Bin collection morning reminder"
    trigger:
      - platform: time
        at: "07:00:00"
    condition:
      - condition: state
        entity_id: calendar.bin_collection_your_address
        state: "on"
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "Bin collection today"
          message: >
            Put the {{ state_attr('calendar.bin_collection_your_address', 'message') | lower }} bin out.
```

---

### Reminder for a specific bin type only

Uses a condition on `trigger.calendar_event.summary` to act only when the recycling bin is due.

```yaml
automation:
  - alias: "Recycling collection reminder"
    triggers:
      - trigger: calendar
        event: start
        entity_id: calendar.bin_collection_your_address
        offset: "-04:00:00"
    conditions:
      - condition: template
        value_template: >
          {{ trigger.calendar_event.summary == 'Recycling' }}
    actions:
      - action: notify.mobile_app_your_phone
        data:
          title: "Recycling tomorrow"
          message: "Don't forget to put the recycling bin out tonight!"
```

---

### Announce on a smart speaker

Plays a spoken reminder through a media player at 7:30 am on any collection morning, naming the bin type.

```yaml
automation:
  - alias: "Bin day announcement"
    trigger:
      - platform: time
        at: "07:30:00"
    condition:
      - condition: state
        entity_id: calendar.bin_collection_your_address
        state: "on"
    action:
      - service: tts.speak
        target:
          entity_id: tts.home_assistant_cloud
        data:
          media_player_entity_id: media_player.kitchen_speaker
          message: >
            Reminder: today is {{ state_attr('calendar.bin_collection_your_address', 'message') | lower }} collection day.
            Please put the bin out.
```

---

### Dashboard card

Add this to a Lovelace dashboard to show all upcoming bin collections in a calendar view.

```yaml
type: calendar
entities:
  - calendar.bin_collection_your_address
```

Or as a template sensor (add to `configuration.yaml`) to show the next collection as a plain sensor:

```yaml
template:
  - sensor:
      - name: "Next bin collection"
        state: >
          {% set event = state_attr('calendar.bin_collection_your_address', 'start_time') %}
          {{ event if event else 'Unknown' }}
        attributes:
          bin_type: >
            {% set event = state_attr('calendar.bin_collection_your_address', 'message') %}
            {{ event if event else 'Unknown' }}
        icon: mdi:trash-can
```

---

## Troubleshooting

**No addresses found for my postcode**
Verify your postcode works on the [Northumberland Council website](https://bincollection.northumberland.gov.uk/start) directly. The integration uses the same lookup.

**Calendar entity shows no events**
Check Home Assistant logs (Settings → System → Logs) and filter for `northumberland_bin_collection`. A warning will appear if the page was fetched but no events could be parsed — this may indicate the council website has changed its HTML structure.

**Integration fails to connect**
If you see connection errors, check that your Home Assistant instance has general internet access.

**Calendar only shows a few weeks of collections**
The council website includes bot-detection JavaScript that limits how much data is returned to automated clients that make repeated requests in a short period. If you have been reloading the integration frequently (for example, while debugging), the server may temporarily respond with only the next few upcoming collections instead of the full year. The warning `bot-detection script is active` will appear in your logs when this happens. Wait for the next automatic weekly refresh, which will usually restore the full schedule. Under normal weekly operation this should not occur.

**Schedule is out of date**
The schedule refreshes once a week at a randomly chosen time. To force an immediate refresh, go to **Settings → Integrations**, find the integration, and select **Reload**.

**Update failed notification**
If the integration cannot reach the website or parse the response, a notification will appear in **Settings → Repairs**. The calendar will continue to display previously fetched data until the next successful update. The notification clears automatically once a refresh succeeds.

---

## How It Works

The integration scrapes the Northumberland bin collection website using a session-based flow:

1. Fetches the start page to obtain a session cookie and CSRF token
2. Submits your postcode to retrieve the list of addresses
3. Submits your address ID to scope the session to your property
4. Fetches the full-year calendar page and parses the HTML table of collection dates

No credentials are stored beyond your postcode and the unique address ID assigned by the council website.

---

## License

MIT
