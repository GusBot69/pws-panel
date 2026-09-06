import Gtk from 'gi://Gtk';
import Adw from 'gi://Adw';
import GLib from 'gi://GLib';
import Soup from 'gi://Soup';

import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

export default class PwsPanelPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();

        // --- Station mode ---
        const modeRow = new Adw.ComboRow({
            title: 'Station mode',
            subtitle: 'home = always your home station · auto = nearest PWS to your location',
        });
        const modeModel = new Gtk.StringList();
        modeModel.append('Home (fixed station)');
        modeModel.append('Auto (nearest PWS)');
        modeRow.model = modeModel;
        modeRow.selected = settings.get_string('station-mode') === 'auto' ? 1 : 0;
        modeRow.connect('notify::selected', row => {
            settings.set_string('station-mode', row.selected === 1 ? 'auto' : 'home');
        });

        // --- Home station: dropdown of known stations + custom entry ---
        // List comes from the bridge (GET /stations); generic fallback stands
        // when the bridge is offline so prefs stay usable.
        const FALLBACK_STATIONS = [];
        const stationRow = new Adw.ComboRow({title: 'Home station'});
        const stationModel = new Gtk.StringList();
        stationRow.model = stationModel;

        const customRow = new Adw.EntryRow({title: 'Custom station ID'});
        customRow.text = settings.get_string('home-station');
        customRow.connect('notify::text', row => {
            const id = row.text.trim().toUpperCase();
            if (id)
                settings.set_string('home-station', id);
        });

        let syncingStation = false;
        function syncStationSelection() {
            const current = settings.get_string('home-station').trim().toUpperCase();
            let idx = -1;
            for (let i = 0; i < stationModel.get_n_items(); i++) {
                if (stationModel.get_string(i) === current) {
                    idx = i;
                    break;
                }
            }
            if (idx < 0)
                idx = stationModel.get_n_items() - 1; // Custom…
            syncingStation = true;
            stationRow.selected = idx;
            syncingStation = false;
            const isCustom = idx === stationModel.get_n_items() - 1;
            customRow.visible = isCustom;
            if (isCustom && customRow.text !== current)
                customRow.text = current;
        }

        function populateStations(list) {
            const seen = [];
            for (const s of [...list, ...FALLBACK_STATIONS]) {
                const id = String(s).trim().toUpperCase();
                if (id && !seen.includes(id))
                    seen.push(id);
            }
            stationModel.splice(0, stationModel.get_n_items(), [...seen, 'Custom…']);
            syncStationSelection();
        }

        stationRow.connect('notify::selected', () => {
            if (syncingStation)
                return;
            const picked = stationModel.get_string(stationRow.selected);
            if (!picked)
                return;
            if (picked === 'Custom…') {
                customRow.visible = true;
                customRow.grab_focus();
            } else {
                settings.set_string('home-station', picked);
                customRow.visible = false;
            }
        });

        populateStations([]); // fallback list immediately; refined when bridge answers
        try {
            const stationSession = new Soup.Session();
            const stationMsg = Soup.Message.new('GET', 'http://127.0.0.1:8766/stations');
            stationSession.send_and_read_async(stationMsg, GLib.PRIORITY_DEFAULT, null,
                (sess, res) => {
                    try {
                        const bytes = sess.send_and_read_finish(res);
                        const data = JSON.parse(new TextDecoder().decode(bytes.get_data()));
                        if (Array.isArray(data?.stations) && data.stations.length)
                            populateStations(data.stations);
                    } catch (e) {
                        // bridge answered garbage — fallback list stands
                    }
                });
        } catch (e) {
            // Soup unavailable — fallback list stands
        }

        // --- Home latitude ---
        const latRow = new Adw.EntryRow({title: 'Home latitude'});
        latRow.text = String(settings.get_double('home-lat'));
        latRow.connect('notify::text', row => {
            const v = parseFloat(row.text);
            if (!isNaN(v))
                settings.set_double('home-lat', v);
        });

        // --- Home longitude ---
        const lonRow = new Adw.EntryRow({title: 'Home longitude'});
        lonRow.text = String(settings.get_double('home-lon'));
        lonRow.connect('notify::text', row => {
            const v = parseFloat(row.text);
            if (!isNaN(v))
                settings.set_double('home-lon', v);
        });

        // --- Home geofence radius ---
        const radiusRow = new Adw.SpinRow({
            title: 'Home geofence radius (km)',
            subtitle: 'Auto mode uses your home station inside this distance of home',
        });
        radiusRow.adjustment = new Gtk.Adjustment({
            lower: 0.5,
            upper: 50.0,
            step_increment: 0.5,
            value: settings.get_double('home-radius-km'),
        });
        radiusRow.connect('notify::value', row => {
            settings.set_double('home-radius-km', row.value);
        });

        // --- Poll interval ---
        const pollRow = new Adw.SpinRow({
            title: 'Poll interval (seconds)',
            subtitle: 'How often weather refreshes. Min 60, default 900.',
        });
        pollRow.adjustment = new Gtk.Adjustment({
            lower: 60,
            upper: 3600,
            step_increment: 60,
            value: settings.get_int('poll-interval'),
        });
        pollRow.connect('notify::value', row => {
            settings.set_int('poll-interval', Math.round(row.value));
        });

        // --- Show label toggle ---
        const labelRow = new Adw.SwitchRow({
            title: 'Show temperature in top bar',
            subtitle: 'Hide to show only the condition emoji',
        });
        labelRow.active = settings.get_boolean('show-label');
        labelRow.connect('notify::active', row => {
            settings.set_boolean('show-label', row.active);
        });

        // Assemble: rows in a group, group in a page, page in the window
        const group = new Adw.PreferencesGroup();
        group.add(modeRow);
        group.add(stationRow);
        group.add(customRow);
        group.add(latRow);
        group.add(lonRow);
        group.add(radiusRow);
        group.add(pollRow);
        group.add(labelRow);

        const page = new Adw.PreferencesPage();
        page.add(group);
        window.add(page);
    }
}
