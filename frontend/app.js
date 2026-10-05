const $ = (s) => document.querySelector(s);
const money = (n) => new Intl.NumberFormat('es-ES', {style:'currency', currency:'EUR'}).format(n || 0);
const esc = (value='') => String(value).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const freshTime = value => value ? new Intl.DateTimeFormat('es-ES',{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}).format(new Date(value)) : 'todavía sin consultar';
let stores = [];
let analysis = null;
let searchSequence = 0;

async function api(path, options = {}) {
  const response = await fetch(path, {headers:{'Content-Type':'application/json'}, ...options});
  if (!response.ok) {
    let message = 'No se pudo completar la acción';
    try { message = (await response.json()).detail || message; } catch (_) {}
    throw new Error(message);
  }
  if (response.status === 204) return null;
  return response.json();
}

function toast(message) {
  const el = $('#toast'); el.textContent = message; el.classList.add('show');
  clearTimeout(window.toastTimer); window.toastTimer = setTimeout(() => el.classList.remove('show'), 2600);
}

async function load() {
  try {
    [stores, analysis] = await Promise.all([api('/api/stores'), api('/api/analysis')]);
    render();
  } catch (error) { toast(error.message); }
}

function render() {
  const selected = stores.filter(s => s.selected && s.live);
  $('#storeCount').textContent = `(${selected.length})`;
  $('#itemCount').textContent = `${analysis.items.length} ${analysis.items.length === 1 ? 'producto' : 'productos'}`;
  const list = $('#shoppingList');
  if (!analysis.items.length) {
    list.innerHTML = '<div class="empty"><strong>Tu lista está vacía</strong>Busca cualquier producto: consultaremos las tiendas seleccionadas.</div>';
  } else {
    list.innerHTML = analysis.items.map(item => `
      <article class="shopping-item ${item.checked ? 'checked':''}">
        <input class="check" type="checkbox" ${item.checked?'checked':''} data-check="${item.id}" aria-label="Marcar ${esc(item.name)} como comprado">
        <div class="item-main">
          <div class="item-head"><span class="product-icon">🛒</span><div><div class="item-name">${esc(item.name)}</div><div class="item-unit">${item.prices.length} precios encontrados · ${freshTime(item.searched_at)}</div></div></div>
          <div class="price-row">${item.prices.map((p,i)=>`<a class="price-chip ${i===0?'best':''} ${p.on_offer?'has-offer':''}" href="${esc(p.on_offer ? p.promotion_url : p.source_url)}" target="_blank" rel="noopener" title="${esc(p.product_name)} · ${esc(p.source)}">${esc(p.store)} · ${money(p.price)}${p.per_unit ? ` · ${money(p.per_unit)}/${esc(p.unit)}`:''}${p.on_offer ? ` <b class="offer-badge">Oferta -${p.discount_pct}%</b>`:''}</a>`).join('') || '<span class="price-chip">Sin coincidencias</span>'}</div>
        </div>
        <div class="item-controls">
          <div class="qty"><button data-qty="${item.id}" data-value="${item.quantity-1}" ${item.quantity===1?'disabled':''} aria-label="Quitar una unidad">−</button><span>${item.quantity}</span><button data-qty="${item.id}" data-value="${item.quantity+1}" aria-label="Añadir una unidad">+</button></div>
          <button class="delete" data-delete="${item.id}" aria-label="Eliminar ${esc(item.name)}">×</button>
        </div>
      </article>`).join('');
  }
  $('#optimalTotal').textContent = money(analysis.optimal_total);
  if (!analysis.items.length) $('#savingsText').textContent = 'Añade productos para calcular tu cesta.';
  else if (analysis.single_best && analysis.savings > 0) $('#savingsText').textContent = `Ahorras ${money(analysis.savings)} frente a comprar todo en ${analysis.single_best.name}.`;
  else if (analysis.single_best) $('#savingsText').textContent = 'La selección prioriza el mejor precio por kg, litro o unidad, aunque un envase grande pueda costar más.';
  else $('#savingsText').textContent = 'Ninguna tienda tiene todavía todos los productos de la lista.';
  const allocations = {};
  analysis.items.forEach(i => { if(i.cheapest) allocations[i.cheapest.store] = (allocations[i.cheapest.store]||0)+1; });
  $('#splitPlan').innerHTML = Object.entries(allocations).map(([store,count])=>`<span class="split-pill">${esc(store)} · ${count} ${count===1?'producto':'productos'}</span>`).join('');
  $('#storeTotals').innerHTML = analysis.store_totals.map((s,i)=>`<div class="store-total"><i class="store-dot" style="background:${s.color}"></i><strong>${esc(s.name)}${i===0&&s.complete?'<small class="best-label">Mejor cesta</small>':''}<small class="coverage">${s.coverage}/${analysis.items.length} productos</small></strong><span>${s.coverage ? money(s.total) : '—'}</span></div>`).join('');
  $('#priceInfo').innerHTML = `<strong>Actualizados ${freshTime(analysis.latest_update)}</strong>${esc(analysis.price_note)} Los enlaces abren la fuente original. Respaldo diario de <a href="https://chollosuper.com" target="_blank" rel="noopener">CholloSúper</a> (CC BY 4.0).`;
  $('#recommendations').innerHTML = analysis.recommendations.map(r=>`<div class="recommendation"><strong>${esc(r.title)}</strong><p>${esc(r.text)}</p></div>`).join('') || '<div class="recommendation"><p>Añade productos para comparar precios reales.</p></div>';
}

let searchTimer;
$('#productSearch').addEventListener('input', event => {
  clearTimeout(searchTimer);
  const q = event.target.value.trim();
  if (q.length < 2) { $('#searchResults').hidden = true; return; }
  const results = $('#searchResults'); results.hidden = false; results.innerHTML = '<div class="search-loading">Consultando supermercados…</div>';
  searchTimer = setTimeout(() => search(q), 650);
});
document.addEventListener('keydown', e => { if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();$('#productSearch').focus();} });

async function search(q) {
  const sequence = ++searchSequence;
  try {
    const result = await api(`/api/live-search?q=${encodeURIComponent(q)}`);
    if (sequence !== searchSequence || $('#productSearch').value.trim() !== q) return;
    const results = $('#searchResults');
    if (!result.offers.length) {
      results.innerHTML = `<div class="search-empty"><strong>No encontramos “${esc(q)}”</strong><br>Prueba con marca, formato o un término más concreto.</div>`;
      return;
    }
    const offerRows = result.offers.map((offer,index)=>`<a class="offer ${offer.on_offer?'has-offer':''}" href="${esc(offer.on_offer ? offer.promotion_url : offer.source_url)}" target="_blank" rel="noopener"><i class="store-dot" style="background:${offer.color}"></i><div><strong>${esc(offer.store)}${offer.on_offer?` <b class="offer-badge">Oferta -${offer.discount_pct}%</b>`:''}</strong><small>${esc(offer.product_name)} · ${esc(offer.source)}</small></div><span class="offer-price">${money(offer.price)}${offer.per_unit?`<small>${money(offer.per_unit)}/${esc(offer.unit)}</small>`:''}${offer.on_offer&&offer.previous_price?`<small class="previous-price">Antes ${money(offer.previous_price)}</small>`:''}</span></a>`).join('');
    const best = result.offers[0];
    const bestLabel = best.per_unit && result.comparison_unit ? `Mejor precio: ${money(best.per_unit)}/${esc(best.unit)}` : `Mejor precio: ${money(best.price)}`;
    results.innerHTML = `<div class="live-result"><div class="live-result-head"><div><strong>${esc(q)}</strong><small>${result.offers.length} tiendas con coincidencia · ordenado por precio comparable</small></div><span class="best-label">${bestLabel}</span></div><div class="offer-list">${offerRows}</div><button class="button primary" data-add-query="${esc(q)}">Añadir y guardar estos precios</button></div>`;
  } catch(error) {
    if (sequence === searchSequence) $('#searchResults').innerHTML = `<div class="search-empty"><strong>No se pudo consultar ahora</strong><br>${esc(error.message)}</div>`;
  }
}

$('#searchResults').addEventListener('click', async e => {
  const button = e.target.closest('[data-add-query]'); if(!button) return;
  e.preventDefault(); button.disabled = true; button.textContent = 'Guardando…';
  try {
    await api('/api/items',{method:'POST',body:JSON.stringify({query:button.dataset.addQuery,quantity:1})});
    $('#productSearch').value=''; $('#searchResults').hidden=true; toast('Producto añadido con precios actuales'); await load();
  } catch(error){button.disabled=false;button.textContent='Añadir y guardar estos precios';toast(error.message)}
});
document.addEventListener('click', e => { if(!e.target.closest('.search-wrap')) $('#searchResults').hidden=true; });

$('#shoppingList').addEventListener('click', async e => {
  const qty = e.target.closest('[data-qty]'), del = e.target.closest('[data-delete]');
  try {
    if(qty) await api(`/api/items/${qty.dataset.qty}`,{method:'PATCH',body:JSON.stringify({quantity:Number(qty.dataset.value)})});
    if(del) await api(`/api/items/${del.dataset.delete}`,{method:'DELETE'});
    if(qty||del) await load();
  } catch(error){toast(error.message)}
});
$('#shoppingList').addEventListener('change', async e => { if(e.target.matches('[data-check]')){await api(`/api/items/${e.target.dataset.check}`,{method:'PATCH',body:JSON.stringify({checked:e.target.checked})});await load();} });

$('#refreshButton').addEventListener('click', async () => {
  const button = $('#refreshButton'); button.disabled=true; button.textContent='Actualizando…';
  try { const result=await api('/api/refresh',{method:'POST'}); toast(result.updated ? `${result.updated} productos actualizados` : 'Añade productos primero'); await load(); }
  catch(error){toast(error.message)} finally {button.disabled=false;button.textContent='Actualizar'}
});

$('#storesButton').addEventListener('click', () => {
  $('#storeOptions').innerHTML = stores.map(s=>`<label class="store-option ${s.live?'':'disabled'}"><input type="checkbox" name="store" value="${s.id}" ${s.selected?'checked':''} ${s.live?'':'disabled'}><i class="store-dot" style="background:${s.color}"></i><span><strong>${esc(s.name)}</strong><small>${esc(s.note)}</small></span></label>`).join('');
  $('#storesDialog').showModal();
});
$('#storesForm').addEventListener('submit', async e => {
  if(e.submitter?.value==='cancel') return;
  e.preventDefault();
  const ids = [...document.querySelectorAll('[name=store]:checked')].map(i=>Number(i.value));
  if(!ids.length){toast('Selecciona al menos un supermercado disponible');return;}
  try{await api('/api/stores/selection',{method:'PUT',body:JSON.stringify({store_ids:ids})});$('#storesDialog').close();toast('Supermercados actualizados');await load();}catch(error){toast(error.message)}
});

load();
