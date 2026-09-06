import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Soup from 'gi://Soup';
import Geoclue from 'gi://Geoclue';

const BRIDGE_BASE = 'http://127.0.0.1:8766';
const DEFAULT_POLL_SECONDS = 900;

const COMPASS = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];

function degToCompass(deg) {
    if (deg === null || deg === undefined || Number.isNaN(Number(deg)))
        return '–';
    return COMPASS[((Math.round(deg / 22.5) % 16) + 16) % 16];
}

// Same Rothfusz/NWS logic as pws-digest.py
function realFeel(tempF, humidity, windMph) {
    if (tempF > 50) {
        if (tempF < 80)
            return {value: tempF, label: 'Real Feel'};
        const t = tempF, rh = humidity;
        let hi = (-42.379 + 2.04901523*t + 10.14333127*rh - 0.22475541*t*rh
                  - 0.00683783*t*t - 0.05481717*rh*rh + 0.00122874*t*t*rh
                  + 0.00085282*t*rh*rh - 0.00000199*t*t*rh*rh);
        if (rh < 13 && t >= 80 && t <= 112)
            hi -= ((13 - rh) / 4) * Math.sqrt((17 - Math.abs(t - 95)) / 17);
        else if (rh > 85 && t >= 80 && t <= 87)
            hi += ((rh - 85) / 10) * ((87 - t) / 5);
        return {value: Math.round(hi * 10) / 10, label: 'Real Feel'};
    }
    if (windMph >= 3) {
        const wc = 35.74 + 0.6215*tempF - 35.75*(windMph**0.16) + 0.4275*tempF*(windMph**0.16);
        return {value: Math.round(wc * 10) / 10, label: 'Wind Chill'};
    }
    return {value: tempF, label: 'Wind Chill'};
}

function fanVerdict(dp) {
    if (dp < 50) return '✅ Great — run the fan';
    if (dp < 55) return '✅ Safe — fan OK';
    if (dp < 60) return '⚠️ Borderline — risky';
    if (dp < 65) return '❌ Too humid — fan off';
    if (dp < 70) return '❌ Oppressive — fan off';
    return '❌ Hell no — fan off';
}

function conditionEmoji(d) {
    if ((d.precip_rate ?? 0) > 0.02) return '🌧️';
    if ((d.solar_radiation ?? 0) > 500) return '☀️';
    if ((d.solar_radiation ?? 0) > 150) return '⛅';
    return '☁️';
}

const PwsPanelButton = GObject.registerClass(
class PwsPanelButton extends PanelMenu.Button {
    _init(onRefresh) {
        super._init(0.0, 'PWS Panel', false);
        this._onRefresh = onRefresh;
        this._label = new St.Label({
            text: '—°',
            y_align: Clutter.ActorAlign.CENTER,
            style_class: 'pws-panel-label',
        });
        this.add_child(this._label);
        this._lastData = null;
        this._items = [];
    }

    setStatus(text) {
        this._label.text = text;
    }

    updateMenu(data) {
        const menu = this.menu;
        if (!menu) {
            this.setStatus('—°');
            return;
        }
        menu.removeAll();
        this._items = [];

        if (!data) {
            this._addItem('❌ PWS bridge offline', null);
            this._addItem('(is pws-bridge.service running?)', null);
            this._addSeparator();
            this._addItem('🔄 Refresh now', () => {
                this.setStatus('…');
                if (this._onRefresh)
                    this._onRefresh();
            });
            return;
        }

        const t = data.temp_f;
        const rf = realFeel(t, data.humidity, data.wind_mph);
        const dp = data.dewpt_f;

        this._addHeader(`🌤  ${data.station ?? 'PWS'}${data.location_label ? ' — ' + data.location_label : ''}`);
        this._addSeparator();
        this._addRow('Temp', `${t}°F`);
        const feel = rf.value > t ? `${rf.value}°F 🔥` : rf.value < t ? `${rf.value}°F 🥶` : `${rf.value}°F`;
        this._addRow(rf.label, feel);
        this._addRow('Humidity', `${data.humidity}%`);
        this._addRow('Dew Point', `${dp}°F`);
        this._addRow('Pressure', `${data.pressure} inHg`);
        this._addRow('Wind', `${data.wind_mph} mph ${degToCompass(data.wind_dir)}`);
        if (data.solar_radiation != null)
            this._addRow('Solar', `${data.solar_radiation} W/m²`);
        if (data.uv_index != null)
            this._addRow('UV Index', `${data.uv_index}`);
        this._addRow('Precip', `${data.precip_rate} in/hr (today: ${data.precip_total}")`);
        this._addSeparator();
        this._addRow('Fan', `${fanVerdict(dp)}`, 'pws-row-fan');
        if (data.aqi != null)
            this._addRow('AQI', `${data.aqi_emoji ?? '⚪'} ${data.aqi} (${data.aqi_level}) — PM2.5: ${data.pm25_aqi}`);
        if (data.river && data.river.stream_level_ft != null) {
            const f = data.river;
            const lvl = parseFloat(f.stream_level_ft);
            const status = lvl >= 686.5 ? '⚠️' : lvl >= 680 ? '🟡' : '🟢';
            this._addRow(data.river.label || 'River gauge', `${status} ${f.stream_level_ft} ft (gates ${f.gate1_pct}/${f.gate2_pct}/${f.gate3_pct})`);
        }
        this._addSeparator();
        this._addRow('Updated', data.fetched_at ?? '?');
        this._addSeparator();
        this._addItem('🔄 Refresh now', () => {
            this.setStatus('…');
            if (this._onRefresh)
                this._onRefresh();
        });
        this._addItem('🌐 Open Wunderground dashboard', () => {
            const st = data.station || this._settings.get_string('home-station') || 'PWS-LOCAL';
            Gio.AppInfo.launch_default_for_uri(`https://www.wunderground.com/dashboard/pws/${encodeURIComponent(st)}`, null);
        });
    }

    _addHeader(text) {
        const item = new PopupMenu.PopupMenuItem(text, {reactive: false});
        item.add_style_class_name('pws-menu-header');
        this.menu.addMenuItem(item);
        this._items.push(item);
    }

    _addSeparator() {
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
    }

    _addRow(label, value, extraClass) {
        const item = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        const box = new St.BoxLayout({style_class: 'pws-row'});
        const labelLbl = new St.Label({
            text: label,
            x_expand: true,
            x_align: Clutter.ActorAlign.START,
        });
        const valueLbl = new St.Label({
            text: value,
            x_align: Clutter.ActorAlign.END,
            style_class: 'pws-row-value',
        });
        box.add_child(labelLbl);
        box.add_child(valueLbl);
        item.add_child(box);
        if (extraClass)
            item.add_style_class_name(extraClass);
        this.menu.addMenuItem(item);
        this._items.push(item);
    }

    _addItem(text, onActivate) {
        const item = new PopupMenu.PopupMenuItem(text, {reactive: !!onActivate});
        if (onActivate)
            item.connect('activate', onActivate);
        this.menu.addMenuItem(item);
        this._items.push(item);
    }
});

export default class PwsPanelExtension extends Extension {
    enable() {
        try {
            this._session = new Soup.Session();
            this._session.timeout = 15; // never hang a refresh forever
            this._settings = this.getSettings();
            if (!this._settings) {
                console.error('PWS Panel: GSettings schema not found — run glib-compile-schemas');
                return;
            }
            this._geoclue = null;

            this._button = new PwsPanelButton(
                () => this._refresh());
            Main.panel.addToStatusArea('pws-panel', this._button, 0, 'center');

            // React to settings changes live
            this._settingsChangedId = this._settings.connect('changed', () => {
                this._rearmTimer();
                this._refresh();
            });

            this._rearmTimer();
            this._refresh();
        } catch (e) {
            console.error(`PWS Panel enable failed: ${e}`);
            this.disable();
        }
    }

    disable() {
        if (this._timer) {
            GLib.source_remove(this._timer);
            this._timer = null;
        }
        if (this._settingsChangedId) {
            this._settings.disconnect(this._settingsChangedId);
            this._settingsChangedId = null;
        }
        this._session?.abort();
        this._session = null;
        this._button?.destroy();
        this._button = null;
    }

    _rearmTimer() {
        if (this._timer)
            GLib.source_remove(this._timer);
        const interval = this._settings.get_int('poll-interval');
        this._timer = GLib.timeout_add_seconds(
            GLib.PRIORITY_DEFAULT,
            Math.max(60, interval),
            () => {
                this._refresh();
                return GLib.SOURCE_CONTINUE;
            });
    }

    _currentStationUrl() {
        const mode = this._settings.get_string('station-mode');
        const home = this._settings.get_string('home-station') || 'PWS-LOCAL';

        if (mode === 'home')
            return Promise.resolve(`${BRIDGE_BASE}/pws?station=${encodeURIComponent(home)}`);

        // auto: geofence-check home first, else resolve nearest PWS
        let homeLat = 0.0, homeLon = 0.0, radius = 5.0;
        try {
            homeLat = this._settings.get_double('home-lat');
            homeLon = this._settings.get_double('home-lon');
            radius = this._settings.get_double('home-radius-km');
        } catch (e) {
            console.warn(`PWS Panel: settings read failed, using defaults: ${e}`);
        }
        return this._getLocation()
            .then(loc => {
                // Manual URL build — avoids URLSearchParams availability issues
                const qs = `lat=${loc.lat}&lon=${loc.lon}&home-lat=${homeLat}&home-lon=${homeLon}&home-radius=${radius}`;
                return `${BRIDGE_BASE}/pws?${qs}`;
            })
            .catch(() => `${BRIDGE_BASE}/pws?station=${encodeURIComponent(home)}`);
    }

    _getLocation() {
        return new Promise((resolve, reject) => {
            try {
                if (!this._geoclue) {
                    this._geoclue = new Geoclue.Simple({
                        desktop_id: 'pws-panel@fedora',
                        accuracy_level: Geoclue.AccuracyLevel.NEIGHBORHOOD,
                    });
                }
                this._geoclue.get_location_async(null, (src, res) => {
                    try {
                        const loc = src.get_location_finish(res);
                        const lat = loc.latitude;
                        const lon = loc.longitude;
                        if (lat === 0 && lon === 0)
                            reject(new Error('location unavailable'));
                        else
                            resolve({lat, lon});
                    } catch (e) {
                        reject(e);
                    }
                });
            } catch (e) {
                reject(e);
            }
        });
    }

    _refresh() {
        if (!this._button || !this._session)
            return;

        this._currentStationUrl().then(url => {
            const message = Soup.Message.new('GET', url);
            this._session.send_and_read_async(message, GLib.PRIORITY_DEFAULT, null,
                (session, res) => {
                    try {
                        const bytes = session.send_and_read_finish(res);
                        const text = new TextDecoder().decode(bytes.get_data());
                        const data = JSON.parse(text);
                        if (!data?.ok)
                            throw new Error('bridge returned error');
                        if (this._settings.get_boolean('show-label'))
                            this._button.setStatus(`${data.temp_f}° ${conditionEmoji(data)}`);
                        else
                            this._button.setStatus(conditionEmoji(data));
                        this._button.updateMenu(data);
                    } catch (e) {
                        this._button.setStatus('—°');
                        this._button.updateMenu(null);
                    }
                });
        }).catch(() => {
            this._button.setStatus('—°');
            this._button.updateMenu(null);
        });
    }
}
