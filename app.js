/* Health HeiGIT Isochrone — aucune géométrie ni statistique simulée. */

const STORAGE =
  'https://hot.storage.heigit.org/heigit-hdx-public';

const TILES =
  `${STORAGE}/access/aux/tiles`;

const COUNTRIES_URL =
  `${STORAGE}/access/aux/countries.yaml`;

const COLORS = [
  '#fde725',
  '#c2df23',
  '#86d549',
  '#52c569',
  '#2ab07f',
  '#1e9b8a',
  '#25858e',
  '#2d708e',
  '#38588c',
  '#433e85',
  '#482173',
  '#440154'
];

const RANGES =
  Array.from({ length: 12 }, (_, index) => (index + 1) * 600);

const FALLBACK_COUNTRIES = [
  ['ben', 'Bénin'],
  ['bfa', 'Burkina Faso'],
  ['civ', 'Côte d’Ivoire'],
  ['cmr', 'Cameroun'],
  ['cod', 'République démocratique du Congo'],
  ['eth', 'Éthiopie'],
  ['gha', 'Ghana'],
  ['gin', 'Guinée'],
  ['hti', 'Haïti'],
  ['ken', 'Kenya'],
  ['mdg', 'Madagascar'],
  ['mli', 'Mali'],
  ['moz', 'Mozambique'],
  ['ner', 'Niger'],
  ['nga', 'Nigéria'],
  ['pak', 'Pakistan'],
  ['rwa', 'Rwanda'],
  ['sen', 'Sénégal'],
  ['tcd', 'Tchad'],
  ['tgo', 'Togo'],
  ['uga', 'Ouganda']
];

const POP_LABELS = {
  total: 'Population totale',
  female: 'Femmes',
  male: 'Hommes',
  children: 'Enfants',
  school_age: 'Âge scolaire',
  women_childbearing: 'Femmes en âge de procréer',
  women_of_reproductive_age: 'Femmes en âge de procréer',
  elderly: 'Personnes âgées',
  adults: 'Adultes',
  youth: 'Jeunes',
  under_5: 'Moins de 5 ans'
};

const $ = id => document.getElementById(id);

const state = {
  country: '',
  countryName: '',
  category: 'hospitals',
  facilities: [],
  stats: new Map(),
  populationTypes: [],
  layerName: '',
  bounds: null,
  drawing: false,
  dataReady: false,
  statsReady: false,
  loadId: 0,
  selectedRanges: new Set(RANGES),
  markers: []
};

/* -------------------------------------------------------------------------- */
/* Carte                                                                      */
/* -------------------------------------------------------------------------- */

const protocol = new pmtiles.Protocol();

maplibregl.addProtocol('pmtiles', protocol.tile);

const map = new maplibregl.Map({
  container: 'map',
  style: 'https://tiles.openfreemap.org/styles/positron',
  center: [15, 2],
  zoom: 2.3,
  attributionControl: false
});

map.addControl(
  new maplibregl.NavigationControl({
    showCompass: false
  }),
  'top-right'
);

map.addControl(
  new maplibregl.AttributionControl({
    compact: true
  }),
  'bottom-right'
);

/* -------------------------------------------------------------------------- */
/* Fonctions générales                                                        */
/* -------------------------------------------------------------------------- */

function esc(value = '') {
  return String(value).replace(
    /[&<>'"]/g,
    character => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      "'": '&#39;',
      '"': '&quot;'
    })[character]
  );
}

function formatNumber(value) {
  if (
    value === null ||
    value === undefined ||
    !Number.isFinite(Number(value))
  ) {
    return '—';
  }

  return new Intl.NumberFormat('fr-FR', {
    maximumFractionDigits: 0
  }).format(Number(value));
}

function setStatus(kind, text) {
  $('source-dot').className = `status-dot ${kind}`;
  $('source-status').textContent = text;
}

function notice(text, kind = '') {
  $('notice').textContent = text;
  $('notice').className = `notice ${kind}`.trim();
}

function setLoading(active) {
  $('map-loader').classList.toggle('hidden', !active);
  $('country').disabled = active;
  $('category').disabled = active;
}

function countryLabel(code) {
  const option =
    [...$('country').options].find(item => item.value === code);

  return option?.textContent || code.toUpperCase();
}

function hdxSlug(name) {
  return name
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');
}

/* -------------------------------------------------------------------------- */
/* Catalogue des pays                                                         */
/* -------------------------------------------------------------------------- */

async function loadCountries() {
  let countries;

  try {
    const response = await fetch(COUNTRIES_URL);

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const parsed = jsyaml.load(await response.text());

    countries = Object.entries(parsed)
      .filter(([, value]) => {
        return value && (value.name || value.slug);
      })
      .map(([code, value]) => {
        const slug = Array.isArray(value.slug)
          ? value.slug[0]
          : value.slug;

        const label =
          value.name ||
          String(slug)
            .split('-')
            .map(part => {
              return part[0]?.toUpperCase() + part.slice(1);
            })
            .join(' ');

        return [code.toLowerCase(), label];
      })
      .sort((a, b) => {
        return a[1].localeCompare(b[1], 'fr');
      });

    setStatus(
      'idle',
      `${countries.length} pays dans le catalogue HeiGIT`
    );
  } catch (error) {
    console.warn(
      'Catalogue HeiGIT indisponible',
      error
    );

    countries = FALLBACK_COUNTRIES;

    setStatus(
      'error',
      'Catalogue HeiGIT temporairement indisponible'
    );

    notice(
      'Le serveur HeiGIT ne répond pas. Une liste de secours est affichée, ' +
      'mais aucun résultat ne sera inventé si les données du pays restent ' +
      'indisponibles.',
      'error'
    );
  }

  $('country').innerHTML =
    '<option value="">Sélectionner un pays…</option>' +
    countries
      .map(([code, name]) => {
        return `<option value="${esc(code)}">${esc(name)}</option>`;
      })
      .join('');
}

/* -------------------------------------------------------------------------- */
/* Adresses des données                                                       */
/* -------------------------------------------------------------------------- */

function isoUrl(
  country = state.country,
  category = state.category
) {
  return (
    `${TILES}/${country}/` +
    `${country}_${category}_isochrones.pmtiles`
  );
}

function statsUrl(
  country = state.country,
  category = state.category
) {
  return (
    `${STORAGE}/access/aux/stats/${country}/` +
    `category=${category}/data.parquet`
  );
}

/* -------------------------------------------------------------------------- */
/* Gestion des couches                                                        */
/* -------------------------------------------------------------------------- */

function removeDataLayers() {
  [
    'facility-label',
    'facility-points',
    'iso-line',
    'iso-fill'
  ].forEach(id => {
    if (map.getLayer(id)) {
      map.removeLayer(id);
    }
  });

  [
    'facilities',
    'isochrones'
  ].forEach(id => {
    if (map.getSource(id)) {
      map.removeSource(id);
    }
  });
}

function rangeColorExpression() {
  const expression = [
    'step',
    ['to-number', ['get', 'range']],
    COLORS[0]
  ];

  for (let index = 1; index < RANGES.length; index += 1) {
    expression.push(
      RANGES[index - 1] + 1,
      COLORS[index]
    );
  }

  return expression;
}

/* -------------------------------------------------------------------------- */
/* Sélection multiple des isochrones                                          */
/* -------------------------------------------------------------------------- */

function renderLegend() {
  $('legend-items').innerHTML =
    RANGES
      .map((range, index) => {
        const checked =
          state.selectedRanges.has(range)
            ? 'checked'
            : '';

        return `
          <label class="legend-item">
            <input
              type="checkbox"
              value="${range}"
              ${checked}
            >

            <i style="background:${COLORS[index]}"></i>

            ${range / 60} min
          </label>
        `;
      })
      .join('');

  $('legend-items')
    .querySelectorAll('input')
    .forEach(input => {
      input.addEventListener('change', () => {
        const range = Number(input.value);

        if (input.checked) {
          state.selectedRanges.add(range);
        } else {
          state.selectedRanges.delete(range);
        }

        updateRangeVisibility();
        renderCharts();
      });
    });
}

function updateRangeVisibility() {
  if (!map.getLayer('iso-fill')) {
    return;
  }

  const selected = [...state.selectedRanges];

  map.setPaintProperty(
    'iso-fill',
    'fill-opacity',
    [
      'case',
      [
        'in',
        ['to-number', ['get', 'range']],
        ['literal', selected]
      ],
      0.67,
      0
    ]
  );

  map.setPaintProperty(
    'iso-line',
    'line-opacity',
    [
      'case',
      [
        'in',
        ['to-number', ['get', 'range']],
        ['literal', selected]
      ],
      0.5,
      0
    ]
  );
}

renderLegend();

/* -------------------------------------------------------------------------- */
/* Statistiques Parquet                                                       */
/* -------------------------------------------------------------------------- */

async function loadStats(loadId) {
  state.stats = new Map();
  state.populationTypes = [];
  state.statsReady = false;

  try {
    const {
      asyncBufferFromUrl,
      parquetReadObjects
    } = await import(
      'https://cdn.jsdelivr.net/npm/hyparquet/src/hyparquet.min.js'
    );

    const file = await asyncBufferFromUrl({
      url: statsUrl()
    });

    const rows = await parquetReadObjects({
      file,
      columns: [
        'range',
        'population_type',
        'population',
        'population_share',
        'admin_level'
      ]
    });

    if (loadId !== state.loadId) {
      return;
    }

    for (const row of rows) {
      if (String(row.admin_level) !== 'ADM0') {
        continue;
      }

      const range = Number(row.range);
      const type = String(row.population_type);

      if (!Number.isFinite(range) || !type) {
        continue;
      }

      if (!state.stats.has(range)) {
        state.stats.set(range, {});
      }

      state.stats.get(range)[type] = {
        population: Number(row.population),
        share: Number(row.population_share)
      };

      if (!state.populationTypes.includes(type)) {
        state.populationTypes.push(type);
      }
    }

    if (!state.stats.size) {
      throw new Error(
        'aucune ligne ADM0 dans le Parquet'
      );
    }

    state.statsReady = true;

    renderPopulationOptions();
    renderChartRangeOptions();
    renderCharts();
  } catch (error) {
    console.error(
      'Statistiques Parquet indisponibles',
      error
    );

    if (loadId === state.loadId) {
      notice(
        'Les isochrones sont chargées, mais le Parquet WorldPop ' +
        `n’a pas pu être lu : ${error.message}`,
        'error'
      );
    }
  }
}

function renderPopulationOptions() {
  const previous =
    $('population-type').value;

  const ordered =
    [...state.populationTypes].sort((a, b) => {
      if (a === 'total') {
        return -1;
      }

      if (b === 'total') {
        return 1;
      }

      return (
        POP_LABELS[a] || a
      ).localeCompare(
        POP_LABELS[b] || b,
        'fr'
      );
    });

  $('population-type').innerHTML =
    ordered
      .map(type => {
        const label =
          POP_LABELS[type] ||
          type.replaceAll('_', ' ');

        return `
          <option value="${esc(type)}">
            ${esc(label)}
          </option>
        `;
      })
      .join('');

  $('population-type').value =
    ordered.includes(previous)
      ? previous
      : (
        ordered.includes('total')
          ? 'total'
          : ordered[0] || 'total'
      );
}

function renderChartRangeOptions() {
  const previous =
    Number($('chart-range').value);

  const ranges =
    [...state.stats.keys()]
      .sort((a, b) => a - b);

  $('chart-range').innerHTML =
    ranges
      .map(range => {
        return `
          <option value="${range}">
            ${range / 60} min
          </option>
        `;
      })
      .join('');

  $('chart-range').value =
    String(
      ranges.includes(previous)
        ? previous
        : (
          ranges.includes(1800)
            ? 1800
            : ranges[0] || ''
        )
    );
}

/* -------------------------------------------------------------------------- */
/* Graphiques                                                                 */
/* -------------------------------------------------------------------------- */

function barsHtml(items, colorByRange = false) {
  const maximum =
    Math.max(
      ...items.map(item => Number(item.value) || 0),
      1
    );

  return items
    .map((item, index) => {
      const muted =
        item.active === false
          ? 'muted'
          : '';

      const width =
        Math.max(
          1,
          (Number(item.value) || 0) / maximum * 100
        );

      const color =
        colorByRange
          ? COLORS[index]
          : '#159170';

      return `
        <div class="chart-row ${muted}">
          <span>${esc(item.label)}</span>

          <div>
            <i
              style="
                width:${width}%;
                background:${color}
              "
            ></i>
          </div>

          <b>${formatNumber(item.value)}</b>
        </div>
      `;
    })
    .join('');
}

function renderCharts() {
  if (!state.statsReady) {
    $('access-chart').innerHTML =
      '<p class="chart-empty">' +
      'Statistiques démographiques indisponibles.' +
      '</p>';

    $('demographic-chart').innerHTML =
      '<p class="chart-empty">' +
      'Statistiques démographiques indisponibles.' +
      '</p>';

    return;
  }

  const type =
    $('population-type').value;

  const curve =
    RANGES
      .map((range, index) => {
        return {
          label: `${range / 60} min`,
          value:
            state.stats.get(range)?.[type]?.population,
          active:
            state.selectedRanges.has(range),
          index
        };
      })
      .filter(item => {
        return Number.isFinite(item.value);
      });

  $('curve-caption').textContent =
    `${displayPopLabel()} · seuils sélectionnés en couleur`;

  $('access-chart').innerHTML =
    curve.length
      ? barsHtml(curve, true)
      : (
        '<p class="chart-empty">' +
        'Indicateur absent pour ces seuils.' +
        '</p>'
      );

  const range =
    Number($('chart-range').value);

  const byType =
    state.stats.get(range) || {};

  const demographics =
    Object.entries(byType)
      .filter(([key, value]) => {
        return (
          key !== 'total' &&
          Number.isFinite(value.population)
        );
      })
      .map(([key, value]) => {
        return {
          label:
            POP_LABELS[key] ||
            key.replaceAll('_', ' '),
          value:
            value.population
        };
      });

  $('demographic-chart').innerHTML =
    demographics.length
      ? barsHtml(demographics)
      : (
        '<p class="chart-empty">' +
        'Aucun groupe démographique détaillé pour ce seuil.' +
        '</p>'
      );
}

/* -------------------------------------------------------------------------- */
/* Chargement d’un pays                                                       */
/* -------------------------------------------------------------------------- */

async function loadCountryData() {
  state.country =
    $('country').value;

  state.countryName =
    countryLabel(state.country);

  state.category =
    $('category').value;

  const loadId =
    ++state.loadId;

  state.dataReady = false;
  state.statsReady = false;
  state.stats = new Map();

  if (!state.country) {
    removeDataLayers();

    setStatus(
      'idle',
      'Sélectionnez un pays'
    );

    render();

    return;
  }

  setLoading(true);

  setStatus(
    'loading',
    `Chargement de ${state.countryName}…`
  );

  notice(
    'Connexion au stockage officiel HeiGIT : ' +
    'aucune donnée de substitution ne sera utilisée.'
  );

  $('dataset-check').textContent = '…';

  $('hdx-link').href =
    'https://data.humdata.org/dataset/' +
    `${hdxSlug(state.countryName)}-accessibility-indicators`;

  try {
    if (!map.isStyleLoaded()) {
      await new Promise(resolve => {
        map.once('load', resolve);
      });
    }

    removeDataLayers();

    const pmtilesFile =
      new pmtiles.PMTiles(isoUrl());

    const [header, metadata] =
      await Promise.all([
        pmtilesFile.getHeader(),
        pmtilesFile.getMetadata()
      ]);

    if (loadId !== state.loadId) {
      return;
    }

    state.bounds = [
      [header.minLon, header.minLat],
      [header.maxLon, header.maxLat]
    ];

    state.layerName =
      metadata?.vector_layers?.[0]?.id ||
      `${state.country}_${state.category}_isochrones`;

    map.addSource('isochrones', {
      type: 'vector',
      url: `pmtiles://${isoUrl()}`,
      attribution: '© HeiGIT · OpenStreetMap'
    });

    map.addLayer({
      id: 'iso-fill',
      type: 'fill',
      source: 'isochrones',
      'source-layer': state.layerName,
      paint: {
        'fill-color': rangeColorExpression(),
        'fill-opacity': 0.67
      }
    });

    map.addLayer({
      id: 'iso-line',
      type: 'line',
      source: 'isochrones',
      'source-layer': state.layerName,
      paint: {
        'line-color': 'rgba(20,48,45,.35)',
        'line-width': 0.35
      }
    });

    updateRangeVisibility();
    addFacilityLayers();

    map.fitBounds(
      state.bounds,
      {
        padding: 35,
        duration: 700
      }
    );

    state.dataReady = true;

    const statsPromise =
      loadStats(loadId);

    await new Promise(resolve => {
      map.once('idle', resolve);
    });

    await statsPromise;

    if (loadId !== state.loadId) {
      return;
    }

    analyzeFacilities();

    $('dataset-check').textContent = '✓';

    setStatus(
      'ready',
      `${state.countryName} · données HeiGIT chargées`
    );

    notice(
      state.statsReady
        ? (
          'Isochrones HeiGIT et agrégats WorldPop chargés. ' +
          'Importez ou dessinez vos structures.'
        )
        : (
          'Isochrones HeiGIT chargées. ' +
          'Les statistiques démographiques sont indisponibles.'
        ),
      state.statsReady
        ? 'success'
        : 'error'
    );
  } catch (error) {
    console.error(error);

    if (loadId !== state.loadId) {
      return;
    }

    removeDataLayers();
    addFacilityLayers();

    $('dataset-check').textContent = '×';

    setStatus(
      'error',
      `Données indisponibles pour ${state.countryName}`
    );

    notice(
      `Impossible de charger les données officielles (${error.message}). ` +
      'Aucun cercle ni chiffre de remplacement n’est affiché.',
      'error'
    );

    render();
  } finally {
    if (loadId === state.loadId) {
      setLoading(false);
    }
  }
}

/* -------------------------------------------------------------------------- */
/* Structures de santé                                                        */
/* -------------------------------------------------------------------------- */

function facilityGeoJSON() {
  return {
    type: 'FeatureCollection',

    features:
      state.facilities.map((facility, index) => {
        return {
          type: 'Feature',
          id: index,

          properties: {
            name: facility.name
          },

          geometry: {
            type: 'Point',

            coordinates: [
              facility.lng,
              facility.lat
            ]
          }
        };
      })
  };
}

function addFacilityLayers() {
  if (
    !map.isStyleLoaded() ||
    map.getSource('facilities')
  ) {
    return;
  }

  map.addSource('facilities', {
    type: 'geojson',
    data: facilityGeoJSON()
  });

  map.addLayer({
    id: 'facility-points',
    type: 'circle',
    source: 'facilities',

    paint: {
      'circle-radius': 7,
      'circle-color': '#ed795d',
      'circle-stroke-color': '#ffffff',
      'circle-stroke-width': 2
    }
  });

  map.addLayer({
    id: 'facility-label',
    type: 'symbol',
    source: 'facilities',

    layout: {
      'text-field': ['get', 'name'],
      'text-size': 10,
      'text-offset': [0, 1.3],
      'text-anchor': 'top',
      'text-allow-overlap': false
    },

    paint: {
      'text-color': '#14302d',
      'text-halo-color': '#ffffff',
      'text-halo-width': 1.5
    }
  });
}

function updateFacilitySource() {
  if (!map.isStyleLoaded()) {
    return;
  }

  if (!map.getSource('facilities')) {
    addFacilityLayers();
  }

  map
    .getSource('facilities')
    ?.setData(facilityGeoJSON());

  /*
   * Les marqueurs HTML restent visibles indépendamment de l’ordre
   * des couches vectorielles. Cela corrige les points dessinés
   * qui étaient parfois masqués sous les isochrones.
   */

  state.markers.forEach(marker => {
    marker.remove();
  });

  state.markers = [];

  state.facilities.forEach(facility => {
    const popup =
      new maplibregl.Popup({
        offset: 22
      }).setHTML(
        popupHtml(facility)
      );

    const marker =
      new maplibregl.Marker({
        color: '#ed795d',
        scale: 0.82
      })
        .setLngLat([
          facility.lng,
          facility.lat
        ])
        .setPopup(popup)
        .addTo(map);

    state.markers.push(marker);
  });
}

/* -------------------------------------------------------------------------- */
/* Jointure spatiale                                                          */
/* -------------------------------------------------------------------------- */

function featureRangeAt(facility) {
  if (
    !state.dataReady ||
    !map.getLayer('iso-fill')
  ) {
    return null;
  }

  const pixel =
    map.project([
      facility.lng,
      facility.lat
    ]);

  const features =
    map.queryRenderedFeatures(
      [
        [pixel.x - 2, pixel.y - 2],
        [pixel.x + 2, pixel.y + 2]
      ],
      {
        layers: ['iso-fill']
      }
    );

  const values =
    features
      .map(feature => {
        return Number(
          feature.properties?.range
        );
      })
      .filter(value => {
        return Number.isFinite(value);
      });

  return values.length
    ? Math.min(...values)
    : null;
}

function analyzeFacilities() {
  state.facilities.forEach(facility => {
    facility.range =
      featureRangeAt(facility);
  });

  render();
}

/* -------------------------------------------------------------------------- */
/* Résultats                                                                  */
/* -------------------------------------------------------------------------- */

function selectedStats(facility) {
  if (
    facility.range === null ||
    facility.range === undefined
  ) {
    return null;
  }

  const byType =
    state.stats.get(
      Number(facility.range)
    );

  if (!byType) {
    return null;
  }

  return (
    byType[$('population-type').value] ||
    null
  );
}

function displayPopLabel() {
  const type =
    $('population-type').value;

  return (
    POP_LABELS[type] ||
    type.replaceAll('_', ' ')
  );
}

function render() {
  updateFacilitySource();

  const query =
    $('search').value
      .trim()
      .toLowerCase();

  const shown =
    state.facilities.filter(facility => {
      return facility.name
        .toLowerCase()
        .includes(query);
    });

  const analyzed =
    state.facilities.filter(facility => {
      return (
        facility.range !== null &&
        facility.range !== undefined
      );
    });

  const sortedRanges =
    analyzed
      .map(facility => facility.range)
      .sort((a, b) => a - b);

  const median =
    sortedRanges.length
      ? (
        sortedRanges[
          Math.floor(
            (sortedRanges.length - 1) / 2
          )
        ] / 60
      )
      : null;

  $('kpi-analyzed').textContent =
    analyzed.length;

  $('kpi-analyzed-sub').textContent =
    `sur ${state.facilities.length} importée` +
    `${state.facilities.length > 1 ? 's' : ''}`;

  $('kpi-time').textContent =
    median === null
      ? '—'
      : `${median} min`;

  $('kpi-classes').textContent =
    new Set(sortedRanges).size;

  $('clear-points').disabled =
    !state.facilities.length;

  $('export-csv').disabled =
    !analyzed.length;

  $('population-heading').textContent =
    `${displayPopLabel().toUpperCase()} ≤ SEUIL`;

  $('table-caption').textContent =
    state.statsReady
      ? (
        'Statistiques nationales cumulées publiées ' +
        'par OpenAccessLens.'
      )
      : (
        'Les valeurs de population restent vides si ' +
        'le Parquet officiel est indisponible.'
      );

  const tbody =
    $('facility-table');

  if (!shown.length) {
    tbody.innerHTML = `
      <tr class="empty">
        <td colspan="5">
          ${
            state.facilities.length
              ? 'Aucune structure ne correspond à la recherche.'
              : (
                'Importez un fichier ou dessinez une structure ' +
                'sur la carte.'
              )
          }
        </td>
      </tr>
    `;
  } else {
    tbody.innerHTML =
      shown
        .map(facility => {
          const stats =
            selectedStats(facility);

          const threshold =
            facility.range === null ||
            facility.range === undefined
              ? 'Hors zone / non chargé'
              : `${facility.range / 60} min`;

          const population =
            stats
              ? formatNumber(stats.population)
              : '—';

          const share =
            stats &&
            Number.isFinite(stats.share)
              ? (
                stats.share.toLocaleString(
                  'fr-FR',
                  {
                    maximumFractionDigits: 1
                  }
                ) + ' %'
              )
              : '—';

          return `
            <tr data-id="${facility.id}">
              <td>${esc(facility.name)}</td>

              <td>
                ${facility.lat.toFixed(5)},
                ${facility.lng.toFixed(5)}
              </td>

              <td>
                <span class="time-badge">
                  ${threshold}
                </span>
              </td>

              <td class="population">
                ${population}
              </td>

              <td>${share}</td>
            </tr>
          `;
        })
        .join('');
  }

  $('file-info').textContent =
    state.facilities.length
      ? (
        `${state.facilities.length} structure` +
        `${state.facilities.length > 1 ? 's' : ''} ` +
        `chargée${state.facilities.length > 1 ? 's' : ''}.`
      )
      : 'Aucune structure chargée.';
}

/* -------------------------------------------------------------------------- */
/* Ajout et import des structures                                             */
/* -------------------------------------------------------------------------- */

function addFacilities(
  items,
  replace = true
) {
  const valid =
    items.filter(item => {
      return (
        Number.isFinite(item.lat) &&
        Number.isFinite(item.lng) &&
        item.lat >= -90 &&
        item.lat <= 90 &&
        item.lng >= -180 &&
        item.lng <= 180
      );
    });

  if (!valid.length) {
    throw new Error(
      'Aucun point valide trouvé'
    );
  }

  const base =
    replace
      ? []
      : state.facilities;

  state.facilities =
    [...base, ...valid].map((facility, index) => {
      return {
        ...facility,
        id: index,
        range: null
      };
    });

  updateFacilitySource();
  render();

  if (state.dataReady) {
    const bounds =
      new maplibregl.LngLatBounds();

    state.facilities.forEach(facility => {
      bounds.extend([
        facility.lng,
        facility.lat
      ]);
    });

    map.fitBounds(
      bounds,
      {
        padding: 80,
        maxZoom: 11
      }
    );

    map.once(
      'idle',
      analyzeFacilities
    );
  } else {
    notice(
      'Structures chargées. Sélectionnez un pays et attendez ' +
      'les données HeiGIT pour lancer la jointure spatiale.'
    );
  }
}

function findColumn(row, names) {
  const keys =
    Object.keys(row);

  const normalize = value => {
    return value
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLowerCase()
      .trim();
  };

  return keys.find(key => {
    return names.includes(
      normalize(key)
    );
  });
}

async function importFile(file) {
  if (/\.csv$/i.test(file.name)) {
    const parsed =
      Papa.parse(
        await file.text(),
        {
          header: true,
          skipEmptyLines: true,
          dynamicTyping: false
        }
      );

    if (
      parsed.errors.length &&
      !parsed.data.length
    ) {
      throw new Error(
        parsed.errors[0].message
      );
    }

    const first =
      parsed.data[0] || {};

    const nameColumn =
      findColumn(
        first,
        [
          'nom',
          'name',
          'facility',
          'etablissement',
          'structure'
        ]
      );

    const latitudeColumn =
      findColumn(
        first,
        [
          'latitude',
          'lat'
        ]
      );

    const longitudeColumn =
      findColumn(
        first,
        [
          'longitude',
          'lon',
          'lng',
          'long'
        ]
      );

    if (
      !latitudeColumn ||
      !longitudeColumn
    ) {
      throw new Error(
        'Colonnes latitude et longitude introuvables'
      );
    }

    const facilities =
      parsed.data.map((row, index) => {
        return {
          name:
            String(
              row[nameColumn] ||
              `Structure ${index + 1}`
            ),

          lat:
            Number(
              String(row[latitudeColumn])
                .replace(',', '.')
            ),

          lng:
            Number(
              String(row[longitudeColumn])
                .replace(',', '.')
            )
        };
      });

    addFacilities(facilities);
  } else if (
    /\.(zip|shp)$/i.test(file.name)
  ) {
    const geo =
      await shp(
        await file.arrayBuffer()
      );

    const collections =
      Array.isArray(geo)
        ? geo
        : [geo];

    const facilities = [];

    collections
      .flatMap(collection => {
        return collection.features || [];
      })
      .forEach((feature, index) => {
        const properties =
          feature.properties || {};

        const name =
          properties.nom ||
          properties.name ||
          properties.NAME ||
          properties.etablissement ||
          properties.facility ||
          `Structure ${index + 1}`;

        if (
          feature.geometry?.type === 'Point'
        ) {
          facilities.push({
            name: String(name),
            lng: Number(
              feature.geometry.coordinates[0]
            ),
            lat: Number(
              feature.geometry.coordinates[1]
            )
          });
        }

        if (
          feature.geometry?.type === 'MultiPoint'
        ) {
          feature.geometry.coordinates.forEach(
            (coordinates, pointIndex) => {
              facilities.push({
                name:
                  `${name} ${pointIndex + 1}`,
                lng:
                  Number(coordinates[0]),
                lat:
                  Number(coordinates[1])
              });
            }
          );
        }
      });

    addFacilities(facilities);
  } else {
    throw new Error(
      'Format non pris en charge'
    );
  }

  notice(
    `${file.name} importé avec succès.`,
    'success'
  );
}

/* -------------------------------------------------------------------------- */
/* Popups                                                                     */
/* -------------------------------------------------------------------------- */

function popupHtml(facility) {
  const all =
    state.stats.get(
      Number(facility.range)
    ) || {};

  const rows =
    Object.entries(all)
      .filter(([type]) => {
        return [
          'total',
          'female',
          'male',
          'under_5',
          'school_age',
          'women_childbearing',
          'elderly'
        ].includes(type);
      })
      .map(([type, stats]) => {
        const label =
          POP_LABELS[type] ||
          type;

        return `
          <dt>${esc(label)}</dt>
          <dd>${formatNumber(stats.population)}</dd>
        `;
      })
      .join('');

  const threshold =
    facility.range === null ||
    facility.range === undefined
      ? 'Classe non déterminée'
      : `${facility.range / 60} minutes`;

  return `
    <div class="popup">
      <h3>${esc(facility.name)}</h3>

      <div class="threshold">
        ${threshold}
      </div>

      ${
        rows
          ? `<dl>${rows}</dl>`
          : (
            '<small>' +
            'Statistiques WorldPop indisponibles.' +
            '</small>'
          )
      }

      <small>
        Population nationale cumulée dans ce seuil
        d’accessibilité ; il ne s’agit pas d’un rayon
        autour de la structure.
      </small>
    </div>
  `;
}

/* -------------------------------------------------------------------------- */
/* Événements de l’interface                                                  */
/* -------------------------------------------------------------------------- */

$('country').addEventListener(
  'change',
  loadCountryData
);

$('category').addEventListener(
  'change',
  loadCountryData
);

$('population-type').addEventListener(
  'change',
  () => {
    render();
    renderCharts();
  }
);

$('chart-range').addEventListener(
  'change',
  renderCharts
);

$('select-all-ranges').addEventListener(
  'click',
  () => {
    state.selectedRanges =
      new Set(RANGES);

    renderLegend();
    updateRangeVisibility();
    renderCharts();
  }
);

$('clear-ranges').addEventListener(
  'click',
  () => {
    state.selectedRanges.clear();

    renderLegend();
    updateRangeVisibility();
    renderCharts();
  }
);

$('search').addEventListener(
  'input',
  render
);

$('facility-file').addEventListener(
  'change',
  async event => {
    const file =
      event.target.files[0];

    if (!file) {
      return;
    }

    try {
      await importFile(file);
    } catch (error) {
      notice(
        `Import impossible : ${error.message}`,
        'error'
      );
    }

    event.target.value = '';
  }
);

$('draw-point').addEventListener(
  'click',
  () => {
    state.drawing =
      !state.drawing;

    $('draw-point').classList.toggle(
      'active',
      state.drawing
    );

    $('draw-point').textContent =
      state.drawing
        ? 'Cliquez sur la carte…'
        : '＋ Dessiner un point';

    map.getCanvas().style.cursor =
      state.drawing
        ? 'crosshair'
        : '';
  }
);

$('clear-points').addEventListener(
  'click',
  () => {
    state.facilities = [];

    updateFacilitySource();
    render();
  }
);

$('fit-country').addEventListener(
  'click',
  () => {
    if (state.bounds) {
      map.fitBounds(
        state.bounds,
        {
          padding: 35
        }
      );
    }
  }
);

/* -------------------------------------------------------------------------- */
/* Export CSV                                                                 */
/* -------------------------------------------------------------------------- */

$('export-csv').addEventListener(
  'click',
  () => {
    const type =
      $('population-type').value;

    const rows = [[
      'nom',
      'latitude',
      'longitude',
      'categorie_heigit',
      'temps_secondes',
      'temps_minutes',
      'population_type',
      'population_cumulee',
      'part_nationale_pct',
      'source'
    ]];

    state.facilities.forEach(facility => {
      const stats =
        selectedStats(facility);

      rows.push([
        facility.name,
        facility.lat,
        facility.lng,
        state.category,
        facility.range ?? '',
        facility.range !== null &&
        facility.range !== undefined
          ? facility.range / 60
          : '',
        type,
        stats?.population ?? '',
        stats?.share ?? '',
        'HeiGIT OpenAccessLens / WorldPop'
      ]);
    });

    const csv =
      rows
        .map(row => {
          return row
            .map(value => {
              return (
                '"' +
                String(value)
                  .replaceAll('"', '""') +
                '"'
              );
            })
            .join(',');
        })
        .join('\n');

    const blob =
      new Blob(
        ['\ufeff' + csv],
        {
          type: 'text/csv;charset=utf-8'
        }
      );

    const url =
      URL.createObjectURL(blob);

    const link =
      document.createElement('a');

    link.href = url;

    link.download =
      `heigit-access-${state.country}-${state.category}.csv`;

    link.click();

    URL.revokeObjectURL(url);
  }
);

/* -------------------------------------------------------------------------- */
/* Clic sur la carte                                                          */
/* -------------------------------------------------------------------------- */

map.on('click', event => {
  if (state.drawing) {
    const number =
      state.facilities.length + 1;

    const name =
      prompt(
        'Nom de la structure :',
        `Structure ${number}`
      );

    if (name !== null) {
      addFacilities(
        [{
          name:
            name.trim() ||
            `Structure ${number}`,

          lat:
            event.lngLat.lat,

          lng:
            event.lngLat.lng
        }],
        false
      );
    }

    state.drawing = false;

    $('draw-point').classList.remove(
      'active'
    );

    $('draw-point').textContent =
      '＋ Dessiner un point';

    map.getCanvas().style.cursor = '';

    return;
  }

  if (map.getLayer('facility-points')) {
    const hit =
      map.queryRenderedFeatures(
        event.point,
        {
          layers: ['facility-points']
        }
      )[0];

    if (hit) {
      const facility =
        state.facilities[
          Number(hit.id)
        ];

      if (facility) {
        new maplibregl.Popup({
          offset: 10
        })
          .setLngLat([
            facility.lng,
            facility.lat
          ])
          .setHTML(
            popupHtml(facility)
          )
          .addTo(map);
      }
    }
  }
});

map.on(
  'mouseenter',
  'facility-points',
  () => {
    if (!state.drawing) {
      map.getCanvas().style.cursor =
        'pointer';
    }
  }
);

map.on(
  'mouseleave',
  'facility-points',
  () => {
    if (!state.drawing) {
      map.getCanvas().style.cursor =
        '';
    }
  }
);

map.on('error', event => {
  console.warn(
    'MapLibre:',
    event.error?.message || event
  );
});

/* -------------------------------------------------------------------------- */
/* Initialisation                                                             */
/* -------------------------------------------------------------------------- */

render();
loadCountries();
