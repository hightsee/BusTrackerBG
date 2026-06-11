# Design

## Visual Theme

Warm, daylight-readable product UI for a city transit tool. The app should feel calm and utilitarian, with restrained personality from Belgrade-specific stop and line content rather than decorative effects.

## Color

- Background: warm tinted neutral, close to parchment but not beige-heavy.
- Surface: lightly elevated warm off-white.
- Text: charcoal, never pure black.
- Accent: muted terracotta for primary actions and selected states.
- Success/status: muted green for direct routes and positive status.
- Danger/warning: muted red for transfer or error emphasis.

## Typography

Use the existing Outfit and Space Mono stack. Outfit carries all product UI copy. Space Mono is reserved for station numbers, line numbers, departure minutes, and compact badges.

## Components

- Primary buttons are large, filled, and reserved for the next obvious action.
- Secondary buttons use outline treatment.
- Line chips are filters, not mandatory setup.
- Stop cards are compact list rows with stop number, name, and served lines.
- Departure cards group by line and show minutes first.
- Maps appear as collapsed or secondary previews unless the user explicitly expands them.

## Layout

Mobile-first, single-column, one-handed. The default screen should show search, fast actions, saved/recent/nearby stops, and no marketing hero. Bottom navigation has three primary tasks: Departures, Route, Saved.

## Motion

Use short transform and opacity transitions only. Motion should confirm taps, reveal loaded content, or open collapsed map/details areas. Avoid decorative or perpetual motion.
