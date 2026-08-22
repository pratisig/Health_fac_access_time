/*
 * Banc d'essai du composant cartographique du mode 1.
 *
 * Le script du composant est exécuté dans un DOM et un réseau simulés afin de
 * vérifier que chaque cause d'échec produit un message précis — et non un
 * message « Chargement… » figé, qui était le symptôme signalé.
 */

const fs = require('fs');
const vm = require('vm');

const html = fs.readFileSync(process.argv[2] || '/tmp/map.html', 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function makeElement(id) {
  return {
    id,
    className: '',
    textContent: '',
    innerHTML: '',
    style: {},
    onclick: null,
    removed: false,
    remove() { this.removed = true; },
  };
}

function buildSandbox(options) {
  const status = makeElement('status');
  const detail = makeElement('detail');
  const listeners = {};
  const timers = [];

  const sandbox = {
    console,
    document: {
      getElementById: (id) => (id === 'status' ? status : id === 'detail' ? detail : null),
      createElement: (tag) => {
        const element = makeElement(tag);
        Object.defineProperty(element, 'src', {
          set(value) {
            element._src = value;
            setImmediate(() => {
              const ok = options.cdnOk === undefined ? true : options.cdnOk(value);
              if (ok) {
                if (/maplibre/.test(value)) sandbox.maplibregl = options.maplibregl;
                if (/pmtiles/.test(value)) sandbox.pmtiles = options.pmtiles;
                element.onload && element.onload();
              } else {
                element.onerror && element.onerror();
              }
            });
          },
          get() { return element._src; },
        });
        return element;
      },
      head: { appendChild() {} },
    },
    window: {
      addEventListener: (name, handler) => { listeners[name] = handler; },
    },
    setTimeout: (fn, ms) => { const t = setTimeout(fn, ms); timers.push(t); return t; },
    clearTimeout,
    setImmediate,
    fetch: options.fetch,
    Math,
    Number,
    Object,
    String,
    TypeError,
    Error,
    Promise,
    JSON,
  };
  sandbox.globalThis = sandbox;
  return { sandbox, status, detail, timers };
}

// ---- Doublures de MapLibre et PMTiles ------------------------------------ //

function fakeMaplibre(options) {
  return {
    addProtocol() {},
    NavigationControl: function () {},
    Marker: function () {
      return { setLngLat() { return this; }, setPopup() { return this; }, addTo() { return this; } };
    },
    Popup: function () {
      return { setLngLat() { return this; }, setHTML() { return this; }, addTo() { return this; } };
    },
    Map: function (config) {
      if (options.requireInlineStyle &&
          (!config || typeof config.style !== 'object' || config.style.version !== 8)) {
        throw new Error('le style dépend encore d’un document distant');
      }
      const handlers = {};
      const map = {
        on(event, handler) { handlers[event] = handler; if (event === 'load' && options.styleLoads) setImmediate(handler); },
        once(event, handler) { handlers[event] = handler; if (event === 'idle') setImmediate(handler); },
        addControl() {}, addSource() {}, addLayer() {}, fitBounds() {},
        queryRenderedFeatures: () => options.unexpected ? [{ properties: { range: 999 } }] : [],
        querySourceFeatures: () => (options.features || []).map((range) => ({ properties: { range } })),
      };
      return map;
    },
  };
}

function fakePmtiles(options) {
  return {
    Protocol: function () { return { tile() {}, add() {} }; },
    PMTiles: function () {
      return {
        getMetadata: async () => options.metadata,
        getHeader: async () => ({ minLon: -17.9, minLat: 12.3, maxLon: -11.3, maxLat: 16.7, minZoom: 0, maxZoom: 12 }),
      };
    },
  };
}

const okMetadata = { vector_layers: [{ id: 'isochrones' }] };

// ---- Scénarios ------------------------------------------------------------ //

const scenarios = [
  {
    name: 'succès complet',
    options: {
      fetch: async () => ({ status: 206 }),
      maplibregl: fakeMaplibre({ styleLoads: true, features: [600, 1200, 1800] }),
      pmtiles: fakePmtiles({ metadata: okMetadata }),
    },
    expect: (status) => status.removed,
    describe: 'le bandeau disparaît',
  },
  {
    name: 'style local sans document distant',
    options: {
      fetch: async () => ({ status: 206 }),
      maplibregl: fakeMaplibre({
        styleLoads: true,
        features: [600, 1200, 1800],
        requireInlineStyle: true,
      }),
      pmtiles: fakePmtiles({ metadata: okMetadata }),
    },
    expect: (status) => status.removed,
    describe: 'buildStyle autonome accepté',
  },
  {
    name: 'CDN injoignable',
    options: {
      cdnOk: () => false,
      fetch: async () => ({ status: 206 }),
    },
    expect: (status) => /aucun CDN joignable/.test(status.innerHTML),
    describe: 'CDN signalé',
  },
  {
    name: 'archive absente (404)',
    options: {
      fetch: async () => ({ status: 404 }),
      maplibregl: fakeMaplibre({ styleLoads: true }),
      pmtiles: fakePmtiles({ metadata: okMetadata }),
    },
    expect: (status) => /404/.test(status.innerHTML) && /introuvable/.test(status.innerHTML),
    describe: '404 signalé',
  },
  {
    name: 'CORS refusé',
    options: {
      fetch: async () => { throw new TypeError('Failed to fetch'); },
      maplibregl: fakeMaplibre({ styleLoads: true }),
      pmtiles: fakePmtiles({ metadata: okMetadata }),
    },
    expect: (status) => /CORS/.test(status.innerHTML),
    describe: 'CORS signalé',
  },
  {
    name: 'fond de carte bloqué',
    options: {
      fetch: async () => ({ status: 206 }),
      maplibregl: fakeMaplibre({ styleLoads: false }),
      pmtiles: fakePmtiles({ metadata: okMetadata }),
    },
    expect: (status) => /fond de carte/.test(status.innerHTML),
    describe: 'style bloqué signalé',
    slow: true,
  },
  {
    name: 'archive sans couche vectorielle',
    options: {
      fetch: async () => ({ status: 206 }),
      maplibregl: fakeMaplibre({ styleLoads: true }),
      pmtiles: fakePmtiles({ metadata: { vector_layers: [] } }),
    },
    expect: (status) => /aucune couche vectorielle/.test(status.innerHTML),
    describe: 'métadonnées vides signalées',
  },
  {
    name: 'aucune entité au zoom courant',
    options: {
      fetch: async () => ({ status: 206 }),
      maplibregl: fakeMaplibre({ styleLoads: true, features: [] }),
      pmtiles: fakePmtiles({ metadata: okMetadata }),
    },
    expect: (status) => /Aucune entité/.test(status.innerHTML) && status.className === 'status warn',
    describe: 'zoom signalé',
  },
  {
    name: 'valeurs de range inattendues',
    options: {
      fetch: async () => ({ status: 206 }),
      maplibregl: fakeMaplibre({ styleLoads: true, features: [999], unexpected: true }),
      pmtiles: fakePmtiles({ metadata: okMetadata }),
    },
    expect: (status) => /inattendues/.test(status.innerHTML),
    describe: 'attribut divergent signalé',
  },
];

(async () => {
  let failures = 0;

  for (const scenario of scenarios) {
    const { sandbox, status, timers } = buildSandbox(scenario.options);
    vm.createContext(sandbox);
    vm.runInContext(script, sandbox);

    await new Promise((resolve) => setTimeout(resolve, scenario.slow ? 15300 : 120));
    timers.forEach(clearTimeout);

    const passed = scenario.expect(status);
    const stuck = !status.removed && /Chargement des PMTiles/.test(status.textContent || '');
    const label = passed && !stuck ? 'OK  ' : 'ÉCHEC';
    if (!passed || stuck) failures += 1;

    console.log(`${label} ${scenario.name.padEnd(32)} → ${scenario.describe}`);
    if (!passed) {
      console.log(`      obtenu : ${(status.innerHTML || status.textContent).slice(0, 160)}`);
    }
  }

  console.log(failures === 0
    ? '\nTous les scénarios produisent un message explicite : aucun blocage sur « Chargement… ».'
    : `\n${failures} scénario(s) en échec.`);
  process.exit(failures === 0 ? 0 : 1);
})();
