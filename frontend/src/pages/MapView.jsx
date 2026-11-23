import React, { useEffect, useRef, useState } from 'react';
import { api, CATEGORY_LABELS, CATEGORY_COLORS } from '../lib/api';
import { useI18n } from '../lib/i18n';
import { PageHead } from './Overview';

export default function MapView() {
  const { t } = useI18n();
  const ref = useRef(null);
  const mapRef = useRef(null);
  const layerRef = useRef(null);
  const [points, setPoints] = useState([]);
  const [cat, setCat] = useState('');

  useEffect(() => {
    (async () => {
      const L = (await import('leaflet')).default;
      await import('leaflet/dist/leaflet.css');
      if (!mapRef.current) {
        mapRef.current = L.map(ref.current, { zoomControl: true, attributionControl: true }).setView([22.5, 79.5], 5);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
          maxZoom: 12, attribution: '&copy; OpenStreetMap contributors',
          className: 'spectra-tiles',
        }).addTo(mapRef.current);
        // Investigative-ops dark map without touching tile pixels (no CORS access
        // to the tile server): a blend-mode pane dims and tints the basemap.
        const pane = mapRef.current.createPane('dark-tint');
        pane.style.zIndex = 250;          // above tiles, below overlays/markers
        pane.style.pointerEvents = 'none';
        pane.style.transition = 'none';
        pane.style.background = 'rgba(6, 10, 18, 0.42)';
        layerRef.current = L.layerGroup().addTo(mapRef.current);
      }
      const data = await api.get('/map');
      setPoints(data.points || []);
      draw(L, data.points || [], cat);
    })();
    return () => { if (mapRef.current) { mapRef.current.remove(); mapRef.current = null; } };
  }, []);

  const draw = async (L, pts, filter) => {
    if (!layerRef.current) return;
    layerRef.current.clearLayers();
    const Lf = L || (await import('leaflet')).default;
    const maxCount = Math.max(1, ...pts.map((p) => p.count));
    pts.filter((p) => !filter || p.categories.includes(filter)).forEach((p) => {
      const r = 10 + 26 * Math.sqrt(p.count / maxCount);
      const col = CATEGORY_COLORS[p.categories[0]] || '#22d3ee';
      Lf.circleMarker([p.lat, p.lng], {
        radius: r, color: col, weight: 1.5, fillColor: col, fillOpacity: 0.28,
      }).addTo(layerRef.current)
        .bindPopup(`<b>${p.name}</b><br/>${p.count} posts<br/>${p.categories.map((c) => CATEGORY_LABELS[c] || c).join(', ') || 'benign'}`);
      Lf.circleMarker([p.lat, p.lng], { radius: 3, color: col, fillColor: col, fillOpacity: 1 }).addTo(layerRef.current);
    });
  };

  const changeCat = async (v) => {
    setCat(v);
    const L = (await import('leaflet')).default;
    draw(L, points, v);
  };

  return (
    <div className="space-y-4">
      <PageHead title={t('map.title')} sub={`${points.length} locations extracted from evidence text (gazetteer)`}
        right={
          <select className="input max-w-[220px]" value={cat} onChange={(e) => changeCat(e.target.value)}>
            <option value="">{t('common.category')}: {t('common.all')}</option>
            {Object.keys(CATEGORY_LABELS).map((c) => <option key={c} value={c}>{CATEGORY_LABELS[c]}</option>)}
          </select>
        } />
      <div className="card overflow-hidden p-0" style={{ height: '72vh' }}>
        <div ref={ref} className="h-full w-full" />
      </div>
      <div className="card p-3">
        <div className="grid grid-cols-2 gap-1 text-xs md:grid-cols-4">
          {[...points].sort((a, b) => b.count - a.count).slice(0, 12).map((p) => (
            <div key={p.name} className="flex items-center justify-between rounded border border-line bg-base/40 px-2 py-1">
              <span className="text-ink/85">{p.name}</span>
              <span className="font-mono text-[10px] text-muted">{p.count}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
