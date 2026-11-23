import React from 'react';
import { Routes, Route } from 'react-router-dom';
import { I18nProvider } from './lib/i18n';
import Layout from './components/Layout';
import Overview from './pages/Overview';
import Ingest from './pages/Ingest';
import Alerts from './pages/Alerts';
import EntityGraph from './pages/EntityGraph';
import Campaigns from './pages/Campaigns';
import Identifiers from './pages/Identifiers';
import Metrics from './pages/Metrics';
import MapView from './pages/MapView';
import Lexicon from './pages/Lexicon';

export default function App() {
  return (
    <I18nProvider>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Overview />} />
          <Route path="ingest" element={<Ingest />} />
          <Route path="alerts" element={<Alerts />} />
          <Route path="graph" element={<EntityGraph />} />
          <Route path="campaigns" element={<Campaigns />} />
          <Route path="identifiers" element={<Identifiers />} />
          <Route path="map" element={<MapView />} />
          <Route path="metrics" element={<Metrics />} />
          <Route path="lexicon" element={<Lexicon />} />
        </Route>
      </Routes>
    </I18nProvider>
  );
}
