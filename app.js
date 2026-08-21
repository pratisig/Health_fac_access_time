/* Health HeiGIT Isochrone — aucune géométrie ni statistique simulée. */
const STORAGE = 'https://hot.storage.heigit.org/heigit-hdx-public';
const TILES = `${STORAGE}/access/aux/tiles`;
const COUNTRIES_URL = `${STORAGE}/access/aux/countries.yaml`;
const COLORS = ['#fde725','#c2df23','#86d549','#52c569','#2ab07f','#1e9b8a','#25858e','#2d708e','#38588c','#433e85','#482173','#440154'];
const RANGES = Array.from({length: 12}, (_, i) => (i + 1) * 600);
const FALLBACK_COUNTRIES = [
  ['ben','Bénin'],['bfa','Burkina Faso'],['civ','Côte d’Ivoire'],['cmr','Cameroun'],['cod','République démocratique du Congo'],
  ['eth','Éthiopie'],['gha','Ghana'],['gin','Guinée'],['hti','Haïti'],['ken','Kenya'],['mdg','Madagascar'],['mli','Mali'],
  ['moz','Mozambique'],['ner','Niger'],['nga','Nigéria'],['pak','Pakistan'],['rwa','Rwanda'],['sen','Sénégal'],['tcd','Tchad'],['tgo','Togo'],['uga','Ouganda']
];
const POP_LABELS = {
  total:'Population totale', female:'Femmes', male:'Hommes', children:'Enfants', school_age:'Âge scolaire',
  women_childbearing:'Femmes en âge de procréer', women_of_reproductive_age:'Femmes en âge de procréer',
  elderly:'Personnes âgées', adults:'Adultes', youth:'Jeunes', under_5:'Moins de 5 ans'
};

const $ = id => document.getElementById(id);
const state = {
  country:'', countryName:'', category:'hospitals', facilities:[], stats:new Map(), populationTypes:[],
  layerName:'', bounds:null, drawing:false, dataReady:false, statsReady:false, loadId:0, db:null, connection:null
};

const protocol = new pmtiles.Protocol();
maplibregl.addProtocol('pmtiles', protocol.tile);
const map = new maplibregl.Map({
  container:'map',
  style:'https://tiles.openfreemap.org/styles/positron',
  center:[15,2], zoom:2.3,
  attributionControl:false
});
map.addControl(new maplibregl.NavigationControl({showCompass:false}), 'top-right');
map.addControl(new maplibregl.AttributionControl({compact:true}), 'bottom-right');

function esc(value='') {
  return String(value).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}
function number(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}
function formatNumber(value) {
  return value === null || value === undefined || !Number.isFinite(Number(value)) ? '—' : new Intl.NumberFormat('fr-FR',{maximumFractionDigits:0}).format(Number(value));
}
function setStatus(kind, text) {
  $('source-dot').className = `status-dot ${kind}`;
  $('source-status').textContent = text;
}
function notice(text, kind='') {
  $('notice').textContent = text;
  $('notice').className = `notice ${kind}`.trim();
}
function setLoading(active) {
  $('map-loader').classList.toggle('hidden', !active);
  $('country').disabled = active;
  $('category').disabled = active;
}
function countryLabel(code) {
  return [...$('country').options].find(o => o.value === code)?.textContent || code.toUpperCase();
}
function hdxSlug(name) {
  return name.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'');
}

async function loadCountries() {
  let countries;
  try {
    const response = await fetch(COUNTRIES_URL);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const parsed = jsyaml.load(await response.text());
    countries = Object.entries(parsed).filter(([,v]) => v && (v.name || v.slug)).map(([code,v]) => {
      const slug = Array.isArray(v.slug) ? v.slug[0] : v.slug;
      const label = v.name || String(slug).split('-').map(x => x[0]?.toUpperCase()+x.slice(1)).join(' ');
      return [code.toLowerCase(), label];
    }).sort((a,b) => a[1].localeCompare(b[1],'fr'));
    setStatus('idle', `${countries.length} pays dans le catalogue HeiGIT`);
  } catch (error) {
    console.warn('Catalogue HeiGIT indisponible', error);
    countries = FALLBACK_COUNTRIES;
    setStatus('error', 'Catalogue HeiGIT temporairement indisponible');
    notice('Le serveur HeiGIT ne répond pas. Une liste de secours est affichée, mais aucun résultat ne sera inventé si les données du pays restent indisponibles.', 'error');
  }
  $('country').innerHTML = '<option value="">Sélectionner un pays…</option>' + countries.map(([code,name]) => `<option value="${esc(code)}">${esc(name)}</option>`).join('');
}

function isoUrl(country=state.country, category=state.category) {
  return `${TILES}/${country}/${country}_${category}_isochrones.pmtiles`;
}
function statsUrl(country=state.country, category=state.category) {
  return `${STORAGE}/access/aux/stats/${country}/category=${category}/data.parquet`;
}
function removeDataLayers() {
  ['facility-label','facility-points','iso-line','iso-fill'].forEach(id => { if (map.getLayer(id)) map.removeLayer(id); });
  ['facilities','isochrones'].forEach(id => { if (map.getSource(id)) map.removeSource(id); });
}
function rangeColorExpression() {
  const expr = ['step',['to-number',['get','range']],COLORS[0]];
  for (let i=1;i<RANGES.length;i++) expr.push(RANGES[i-1]+1,COLORS[i]);
  return expr;
}
function renderLegend() {
  $('legend-items').innerHTML = RANGES.map((r,i) => `<span class="legend-item"><i style="background:${COLORS[i]}"></i>${r/60}</span>`).join('');
}
renderLegend();

async function initDuckDB() {
  if (state.db) return;
  if (!window.duckdb) throw new Error('Moteur analytique DuckDB non chargé');
  const bundles = {
    mvp:{
      mainModule:'https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.29.1/dist/duckdb-mvp.wasm',
      mainWorker:'https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.29.1/dist/duckdb-browser-mvp.worker.js'
    },
    eh:{
      mainModule:'https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.29.1/dist/duckdb-eh.wasm',
      mainWorker:'https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.29.1/dist/duckdb-browser-eh.worker.js'
    }
  };
  const bundle = await duckdb.selectBundle(bundles);
  const workerBlob = new Blob([`importScripts("${bundle.mainWorker}");`], {type:'text/javascript'});
  const worker = new Worker(URL.createObjectURL(workerBlob));
  state.db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(duckdb.LogLevel.WARNING), worker);
  await state.db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  state.connection = await state.db.connect();
}

async function loadStats(loadId) {
  state.stats = new Map(); state.populationTypes = []; state.statsReady = false;
  try {
    await initDuckDB();
    if (loadId !== state.loadId) return;
    const safeUrl = statsUrl().replaceAll("'","''");
    const result = await state.connection.query(`SELECT range, population_type, population, population_share FROM '${safeUrl}' WHERE admin_level = 'ADM0' ORDER BY range`);
    for (const arrowRow of result.toArray()) {
      const row = arrowRow.toJSON();
      const range = Number(row.range), type = String(row.population_type);
      if (!state.stats.has(range)) state.stats.set(range, {});
      state.stats.get(range)[type] = {population:Number(row.population), share:Number(row.population_share)};
      if (!state.populationTypes.includes(type)) state.populationTypes.push(type);
    }
    if (!state.stats.size) throw new Error('aucune ligne ADM0');
    state.statsReady = true;
    renderPopulationOptions();
  } catch (error) {
    console.error('Statistiques Parquet indisponibles', error);
    if (loadId === state.loadId) notice(`Les isochrones peuvent être visibles, mais les statistiques WorldPop n’ont pas pu être lues : ${error.message}`, 'error');
  }
}
function renderPopulationOptions() {
  const previous = $('population-type').value;
  const ordered = [...state.populationTypes].sort((a,b) => (a==='total'?-1:b==='total'?1:(POP_LABELS[a]||a).localeCompare(POP_LABELS[b]||b,'fr')));
  $('population-type').innerHTML = ordered.map(type => `<option value="${esc(type)}">${esc(POP_LABELS[type] || type.replaceAll('_',' '))}</option>`).join('');
  $('population-type').value = ordered.includes(previous) ? previous : (ordered.includes('total')?'total':ordered[0]||'total');
}

async function loadCountryData() {
  state.country = $('country').value;
  state.countryName = countryLabel(state.country);
  state.category = $('category').value;
  const loadId = ++state.loadId;
  state.dataReady = false; state.statsReady = false; state.stats = new Map();
  if (!state.country) {
    removeDataLayers(); setStatus('idle','Sélectionnez un pays'); render(); return;
  }
  setLoading(true); setStatus('loading',`Chargement de ${state.countryName}…`);
  notice('Connexion au stockage officiel HeiGIT : aucune donnée de substitution ne sera utilisée.');
  $('dataset-check').textContent = '…';
  $('hdx-link').href = `https://data.humdata.org/dataset/${hdxSlug(state.countryName)}-accessibility-indicators`;
  try {
    if (!map.isStyleLoaded()) await new Promise(resolve => map.once('load', resolve));
    removeDataLayers();
    const p = new pmtiles.PMTiles(isoUrl());
    const [header, metadata] = await Promise.all([p.getHeader(), p.getMetadata()]);
    if (loadId !== state.loadId) return;
    state.bounds = [[header.minLon,header.minLat],[header.maxLon,header.maxLat]];
    state.layerName = metadata?.vector_layers?.[0]?.id || `${state.country}_${state.category}_isochrones`;
    map.addSource('isochrones',{type:'vector',url:`pmtiles://${isoUrl()}`,attribution:'© HeiGIT · OpenStreetMap'});
    map.addLayer({id:'iso-fill',type:'fill',source:'isochrones','source-layer':state.layerName,paint:{'fill-color':rangeColorExpression(),'fill-opacity':.67}});
    map.addLayer({id:'iso-line',type:'line',source:'isochrones','source-layer':state.layerName,paint:{'line-color':'rgba(20,48,45,.35)','line-width':.35}});
    addFacilityLayers();
    map.fitBounds(state.bounds,{padding:35,duration:700});
    state.dataReady = true;
    const statsPromise = loadStats(loadId);
    await new Promise(resolve => map.once('idle',resolve));
    await statsPromise;
    if (loadId !== state.loadId) return;
    analyzeFacilities();
    $('dataset-check').textContent = '✓';
    setStatus('ready',`${state.countryName} · données HeiGIT chargées`);
    notice(state.statsReady ? 'Isochrones HeiGIT et agrégats WorldPop chargés. Importez ou dessinez vos structures.' : 'Isochrones HeiGIT chargées. Les statistiques démographiques sont indisponibles.', state.statsReady?'success':'error');
  } catch (error) {
    console.error(error);
    if (loadId !== state.loadId) return;
    removeDataLayers(); addFacilityLayers();
    $('dataset-check').textContent = '×';
    setStatus('error',`Données indisponibles pour ${state.countryName}`);
    notice(`Impossible de charger les données officielles (${error.message}). Aucun cercle ni chiffre de remplacement n’est affiché.`, 'error');
    render();
  } finally {
    if (loadId === state.loadId) setLoading(false);
  }
}

function facilityGeoJSON() {
  return {type:'FeatureCollection',features:state.facilities.map((f,i)=>({type:'Feature',id:i,properties:{name:f.name},geometry:{type:'Point',coordinates:[f.lng,f.lat]}}))};
}
function addFacilityLayers() {
  if (!map.isStyleLoaded() || map.getSource('facilities')) return;
  map.addSource('facilities',{type:'geojson',data:facilityGeoJSON()});
  map.addLayer({id:'facility-points',type:'circle',source:'facilities',paint:{'circle-radius':7,'circle-color':'#ed795d','circle-stroke-color':'#fff','circle-stroke-width':2}});
  map.addLayer({id:'facility-label',type:'symbol',source:'facilities',layout:{'text-field':['get','name'],'text-size':10,'text-offset':[0,1.3],'text-anchor':'top','text-allow-overlap':false},paint:{'text-color':'#14302d','text-halo-color':'#fff','text-halo-width':1.5}});
}
function updateFacilitySource() {
  if (!map.isStyleLoaded()) return;
  if (!map.getSource('facilities')) addFacilityLayers();
  map.getSource('facilities')?.setData(facilityGeoJSON());
}
function featureRangeAt(facility) {
  if (!state.dataReady || !map.getLayer('iso-fill')) return null;
  const pixel = map.project([facility.lng,facility.lat]);
  const features = map.queryRenderedFeatures([[pixel.x-2,pixel.y-2],[pixel.x+2,pixel.y+2]],{layers:['iso-fill']});
  const values = features.map(f => Number(f.properties?.range)).filter(Number.isFinite);
  return values.length ? Math.min(...values) : null;
}
function analyzeFacilities() {
  state.facilities.forEach(f => { f.range = featureRangeAt(f); });
  render();
}
function selectedStats(f) {
  if (f.range === null || f.range === undefined) return null;
  const byType = state.stats.get(Number(f.range));
  if (!byType) return null;
  return byType[$('population-type').value] || null;
}
function displayPopLabel() {
  const type = $('population-type').value;
  return POP_LABELS[type] || type.replaceAll('_',' ');
}
function render() {
  updateFacilitySource();
  const q = $('search').value.trim().toLowerCase();
  const shown = state.facilities.filter(f => f.name.toLowerCase().includes(q));
  const analyzed = state.facilities.filter(f => f.range !== null && f.range !== undefined);
  const sortedRanges = analyzed.map(f=>f.range).sort((a,b)=>a-b);
  const median = sortedRanges.length ? (sortedRanges[Math.floor((sortedRanges.length-1)/2)]/60) : null;
  $('kpi-analyzed').textContent = analyzed.length;
  $('kpi-analyzed-sub').textContent = `sur ${state.facilities.length} importée${state.facilities.length>1?'s':''}`;
  $('kpi-time').textContent = median === null ? '—' : `${median} min`;
  $('kpi-classes').textContent = new Set(sortedRanges).size;
  $('clear-points').disabled = !state.facilities.length;
  $('export-csv').disabled = !analyzed.length;
  $('population-heading').textContent = `${displayPopLabel().toUpperCase()} ≤ SEUIL`;
  $('table-caption').textContent = state.statsReady ? 'Statistiques nationales cumulées publiées par OpenAccessLens.' : 'Les valeurs de population restent vides si le Parquet officiel est indisponible.';
  const tbody = $('facility-table');
  if (!shown.length) {
    tbody.innerHTML = `<tr class="empty"><td colspan="5">${state.facilities.length?'Aucune structure ne correspond à la recherche.':'Importez un fichier ou dessinez une structure sur la carte.'}</td></tr>`;
  } else {
    tbody.innerHTML = shown.map(f => {
      const stats = selectedStats(f);
      const threshold = f.range === null || f.range === undefined ? 'Hors zone / non chargé' : `${f.range/60} min`;
      return `<tr data-id="${f.id}"><td>${esc(f.name)}</td><td>${f.lat.toFixed(5)}, ${f.lng.toFixed(5)}</td><td><span class="time-badge">${threshold}</span></td><td class="population">${stats?formatNumber(stats.population):'—'}</td><td>${stats&&Number.isFinite(stats.share)?`${stats.share.toLocaleString('fr-FR',{maximumFractionDigits:1})} %`:'—'}</td></tr>`;
    }).join('');
  }
  $('file-info').textContent = state.facilities.length ? `${state.facilities.length} structure${state.facilities.length>1?'s':''} chargée${state.facilities.length>1?'s':''}.` : 'Aucune structure chargée.';
}

function addFacilities(items, replace=true) {
  const valid = items.filter(f => Number.isFinite(f.lat)&&Number.isFinite(f.lng)&&f.lat>=-90&&f.lat<=90&&f.lng>=-180&&f.lng<=180);
  if (!valid.length) throw new Error('Aucun point valide trouvé');
  const base = replace ? [] : state.facilities;
  state.facilities = [...base,...valid].map((f,i)=>({...f,id:i,range:null}));
  updateFacilitySource(); render();
  if (state.dataReady) {
    const bounds = new maplibregl.LngLatBounds(); state.facilities.forEach(f=>bounds.extend([f.lng,f.lat]));
    map.fitBounds(bounds,{padding:80,maxZoom:11});
    map.once('idle', analyzeFacilities);
  } else notice('Structures chargées. Sélectionnez un pays et attendez les données HeiGIT pour lancer la jointure spatiale.');
}
function findColumn(row, names) {
  const keys = Object.keys(row), normalized = s => s.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().trim();
  return keys.find(k => names.includes(normalized(k)));
}
async function importFile(file) {
  if (/\.csv$/i.test(file.name)) {
    const parsed = Papa.parse(await file.text(),{header:true,skipEmptyLines:true,dynamicTyping:false});
    if (parsed.errors.length && !parsed.data.length) throw new Error(parsed.errors[0].message);
    const first = parsed.data[0] || {}, nameCol=findColumn(first,['nom','name','facility','etablissement','structure']), latCol=findColumn(first,['latitude','lat']), lonCol=findColumn(first,['longitude','lon','lng','long']);
    if (!latCol || !lonCol) throw new Error('Colonnes latitude et longitude introuvables');
    addFacilities(parsed.data.map((r,i)=>({name:String(r[nameCol]||`Structure ${i+1}`),lat:Number(String(r[latCol]).replace(',','.')),lng:Number(String(r[lonCol]).replace(',','.'))})));
  } else if (/\.(zip|shp)$/i.test(file.name)) {
    const geo = await shp(await file.arrayBuffer());
    const collections = Array.isArray(geo) ? geo : [geo], items=[];
    collections.flatMap(c=>c.features||[]).forEach((feature,i) => {
      const p=feature.properties||{}, name=p.nom||p.name||p.NAME||p.etablissement||p.facility||`Structure ${i+1}`;
      if (feature.geometry?.type==='Point') items.push({name:String(name),lng:Number(feature.geometry.coordinates[0]),lat:Number(feature.geometry.coordinates[1])});
      if (feature.geometry?.type==='MultiPoint') feature.geometry.coordinates.forEach((c,j)=>items.push({name:`${name} ${j+1}`,lng:Number(c[0]),lat:Number(c[1])}));
    });
    addFacilities(items);
  } else throw new Error('Format non pris en charge');
  notice(`${file.name} importé avec succès.`, 'success');
}

function popupHtml(f) {
  const all = state.stats.get(Number(f.range)) || {};
  const rows = Object.entries(all).filter(([type]) => ['total','female','male','under_5','school_age','women_childbearing','elderly'].includes(type)).map(([type,s])=>`<dt>${esc(POP_LABELS[type]||type)}</dt><dd>${formatNumber(s.population)}</dd>`).join('');
  return `<div class="popup"><h3>${esc(f.name)}</h3><div class="threshold">${f.range==null?'Classe non déterminée':`${f.range/60} minutes`}</div>${rows?`<dl>${rows}</dl>`:'<small>Statistiques WorldPop indisponibles.</small>'}<small>Population nationale cumulée dans ce seuil d’accessibilité ; il ne s’agit pas d’un rayon autour de la structure.</small></div>`;
}

$('country').addEventListener('change',loadCountryData);
$('category').addEventListener('change',loadCountryData);
$('population-type').addEventListener('change',render);
$('search').addEventListener('input',render);
$('facility-file').addEventListener('change',async e => {
  const file=e.target.files[0]; if(!file)return;
  try{await importFile(file);}catch(error){notice(`Import impossible : ${error.message}`,'error');}
  e.target.value='';
});
$('draw-point').addEventListener('click',()=>{
  state.drawing=!state.drawing; $('draw-point').classList.toggle('active',state.drawing);
  $('draw-point').textContent=state.drawing?'Cliquez sur la carte…':'＋ Dessiner un point';
  map.getCanvas().style.cursor=state.drawing?'crosshair':'';
});
$('clear-points').addEventListener('click',()=>{state.facilities=[];updateFacilitySource();render();});
$('fit-country').addEventListener('click',()=>state.bounds&&map.fitBounds(state.bounds,{padding:35}));
$('export-csv').addEventListener('click',()=>{
  const type=$('population-type').value;
  const rows=[['nom','latitude','longitude','categorie_heigit','temps_secondes','temps_minutes','population_type','population_cumulee','part_nationale_pct','source']];
  state.facilities.forEach(f=>{const s=selectedStats(f);rows.push([f.name,f.lat,f.lng,state.category,f.range??'',f.range!=null?f.range/60:'',type,s?.population??'',s?.share??'','HeiGIT OpenAccessLens / WorldPop']);});
  const csv=rows.map(row=>row.map(v=>`"${String(v).replaceAll('"','""')}"`).join(',')).join('\n');
  const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['\ufeff'+csv],{type:'text/csv;charset=utf-8'}));a.download=`heigit-access-${state.country}-${state.category}.csv`;a.click();URL.revokeObjectURL(a.href);
});
map.on('click',e=>{
  if(state.drawing){
    const n=state.facilities.length+1; const name=prompt('Nom de la structure :',`Structure ${n}`);
    if(name!==null) addFacilities([{name:name.trim()||`Structure ${n}`,lat:e.lngLat.lat,lng:e.lngLat.lng}],false);
    state.drawing=false;$('draw-point').classList.remove('active');$('draw-point').textContent='＋ Dessiner un point';map.getCanvas().style.cursor='';return;
  }
  if(map.getLayer('facility-points')){
    const hit=map.queryRenderedFeatures(e.point,{layers:['facility-points']})[0];
    if(hit){const f=state.facilities[Number(hit.id)];if(f)new maplibregl.Popup({offset:10}).setLngLat([f.lng,f.lat]).setHTML(popupHtml(f)).addTo(map);}
  }
});
map.on('mouseenter','facility-points',()=>{if(!state.drawing)map.getCanvas().style.cursor='pointer'});
map.on('mouseleave','facility-points',()=>{if(!state.drawing)map.getCanvas().style.cursor=''});
map.on('error',e=>console.warn('MapLibre:',e.error?.message||e));

render();
loadCountries();
