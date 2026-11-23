// Minimal i18n (English / Hindi). Usage:
//   const { t } = useI18n(); ... t('nav.overview')
//   <LangToggle /> in the top bar.
import React, { createContext, useContext, useEffect, useMemo, useState } from 'react';

// Local value overrides are read by useLangValue(v) for data-driven labels.
const DICT = {
  en: {
    'app.name': 'SPECTRA',
    'app.tagline': 'Entity Correlation & Threat Recognition',
    'nav.overview': 'Overview',
    'nav.ingest': 'Ingest & Analyze',
    'nav.alerts': 'Alerts',
    'nav.graph': 'Entity Graph',
    'nav.campaigns': 'Campaigns',
    'nav.identifiers': 'Identifier Registry',
    'nav.metrics': 'Model Metrics',
    'nav.map': 'Map',
    'nav.lexicon': 'Lexicon',
    'common.search': 'Search',
    'common.all': 'All',
    'common.refresh': 'Refresh',
    'common.loading': 'Loading…',
    'common.risk': 'Risk',
    'common.category': 'Category',
    'common.channel': 'Channel',
    'common.date': 'Date',
    'common.status': 'Status',
    'common.source': 'Source',
    'common.confidence': 'Confidence',
    'common.export': 'Export CSV',
    'common.report': 'PDF report',
    'common.verify': 'Verify integrity',
    'common.close': 'Close',
    'kpi.posts': 'Posts scanned',
    'kpi.alerts': 'Alerts',
    'kpi.high': 'High risk',
    'kpi.identifiers': 'Unique identifiers',
    'kpi.campaigns': 'Active campaigns',
    'chart.byCategory': 'Alerts by category over time',
    'chart.topChannels': 'Top channels',
    'chart.category': 'Category mix',
    'chart.sources': 'Source breakdown',
    'alerts.title': 'Alerts',
    'alerts.detail': 'Alert detail',
    'alerts.workflow': 'Workflow',
    'alerts.notes': 'Analyst notes',
    'graph.title': 'Entity graph',
    'campaigns.title': 'Coordinated campaigns',
    'identifiers.title': 'Identifier registry',
    'metrics.title': 'Model metrics',
    'metrics.testPost': 'Test a post',
    'map.title': 'Geographic distribution',
    'ingest.title': 'Ingest & analyze',
    'lexicon.title': 'Lexicon editor',
    'status.new': 'New',
    'status.under_review': 'Under review',
    'status.confirmed': 'Confirmed',
    'status.dismissed': 'Dismissed',
  },
  hi: {
    'app.name': 'SPECTRA',
    'app.tagline': 'एंटिटी सहसंबंध और खतरा पहचान',
    'nav.overview': 'अवलोकन',
    'nav.ingest': 'डेटा लोड और विश्लेषण',
    'nav.alerts': 'अलर्ट',
    'nav.graph': 'एंटिटी ग्राफ़',
    'nav.campaigns': 'अभियान',
    'nav.identifiers': 'पहचानकर्ता सूची',
    'nav.metrics': 'मॉडल मेट्रिक्स',
    'nav.map': 'नक्शा',
    'nav.lexicon': 'शब्दकोश',
    'common.search': 'खोजें',
    'common.all': 'सभी',
    'common.refresh': 'रीफ़्रेश',
    'common.loading': 'लोड हो रहा है…',
    'common.risk': 'जोखिम',
    'common.category': 'श्रेणी',
    'common.channel': 'चैनल',
    'common.date': 'दिनांक',
    'common.status': 'स्थिति',
    'common.source': 'स्रोत',
    'common.confidence': 'विश्वास',
    'common.export': 'CSV निर्यात',
    'common.report': 'PDF रिपोर्ट',
    'common.verify': 'अखंडता जाँचें',
    'common.close': 'बंद करें',
    'kpi.posts': 'स्कैन किए पोस्ट',
    'kpi.alerts': 'अलर्ट',
    'kpi.high': 'उच्च जोखिम',
    'kpi.identifiers': 'अद्वितीय पहचानकर्ता',
    'kpi.campaigns': 'सक्रिय अभियान',
    'chart.byCategory': 'समय के साथ श्रेणीवार अलर्ट',
    'chart.topChannels': 'प्रमुख चैनल',
    'chart.category': 'श्रेणी विभाजन',
    'chart.sources': 'स्रोत विभाजन',
    'alerts.title': 'अलर्ट',
    'alerts.detail': 'अलर्ट विवरण',
    'alerts.workflow': 'कार्यप्रवाह',
    'alerts.notes': 'विश्लेषक टिप्पणियाँ',
    'graph.title': 'एंटिटी ग्राफ़',
    'campaigns.title': 'समन्वित अभियान',
    'identifiers.title': 'पहचानकर्ता सूची',
    'metrics.title': 'मॉडल मेट्रिक्स',
    'metrics.testPost': 'पोस्ट जाँचें',
    'map.title': 'भौगोलिक वितरण',
    'ingest.title': 'डेटा लोड और विश्लेषण',
    'lexicon.title': 'शब्दकोश संपादक',
    'status.new': 'नया',
    'status.under_review': 'समीक्षा में',
    'status.confirmed': 'पुष्ट',
    'status.dismissed': 'खारिज',
  },
};

const Ctx = createContext(null);

export function I18nProvider({ children }) {
  const [lang, setLang] = useState(() => localStorage.getItem('spectra.lang') || 'en');
  useEffect(() => { localStorage.setItem('spectra.lang', lang); }, [lang]);
  const value = useMemo(() => ({
    lang,
    setLang,
    t: (key) => DICT[lang][key] || DICT.en[key] || key,
  }), [lang]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useI18n = () => useContext(Ctx);

export function LangToggle() {
  const { lang, setLang } = useI18n();
  return (
    <div className="flex items-center rounded-lg border border-line bg-raised/60 p-0.5 text-[11px]">
      {['en', 'hi'].map((l) => (
        <button
          key={l}
          onClick={() => setLang(l)}
          className={`rounded-md px-2 py-1 font-semibold transition-colors ${lang === l ? 'bg-accent/20 text-accent' : 'text-muted hover:text-ink'}`}
        >
          {l === 'en' ? 'EN' : 'हिं'}
        </button>
      ))}
    </div>
  );
}

// Hindi labels for data-driven category names.
export const CATEGORY_HI = {
  drugs: 'नशीला माल',
  arms: 'हथियार',
  fake_docs: 'फर्जी दस्तावेज़',
  mule_account: 'म्यूल खाता',
  atm_card: 'ATM / डेबिट कार्ड',
  sim_card: 'सिम कार्ड',
  net_banking_kit: 'नेट-बैंकिंग किट',
  mule_fin: 'म्यूल / वित्तीय',
  benign: 'सामान्य',
};
