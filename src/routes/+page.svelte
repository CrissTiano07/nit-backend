<!-- MARK LVII: PROTOCOLO A VISÃO RESTAURADA (VERSÃO CONSOLIDADA E DEFINITIVA) -->
<script>
  import { writable } from 'svelte/store';

  // --- STORES REATIVOS E PERSISTENTES ---
  function createPersistentStore(key, initialValue) {
    const isBrowser = typeof window !== 'undefined';
    let initial = initialValue;
    if (isBrowser) {
      const stored = localStorage.getItem(key);
      if (stored) {
        try {
          const parsed = JSON.parse(stored);
          if (Array.isArray(parsed)) {
            initial = new Map(parsed);
          } else {
            initial = parsed;
          }
        } catch (e) { console.error(`Erro ao carregar store ${key}.`); }
      }
    }
    const store = writable(initial);
    if (isBrowser) {
      store.subscribe(value => {
        localStorage.setItem(key, JSON.stringify(value instanceof Map ? Array.from(value.entries()) : value));
      });
    }
    return store;
  }

  // A MEMÓRIA DE AÇO: Duas fontes de verdade, uma para o histórico, outra para a visão.
  const dailyStore = createPersistentStore('daily-store-v57', new Map());
  const cardsVisuais = createPersistentStore('cards-visuais-v57', new Map());

  // --- ESTADO DA UI ---
  let relatorio = '';
  let alertasToast = [];

  // --- LÓGICA DE NEGÓCIO AUXILIAR ---
  function calcularDisposition(card) {
    const textoCompleto = (card.endereco || '').toLowerCase();
    if (textoCompleto.includes('pgv')) return 'SEM_NECESSIDADE';
    return 'AGUARDANDO';
  }

  // --- MOTOR DE DECISÃO (MARK LVII) ---
  function registrarEvento(codigo, status) {
    dailyStore.update(mapa => {
        if (!mapa.has(codigo)) {
            mapa.set(codigo, { historico: [] });
        }
        const item = mapa.get(codigo);
        item.historico.push({ status, timestamp: new Date().toISOString() });
        item.ultimoStatus = status;
        return mapa;
    });
  }

  function analisarOcorrencia(codigo, novoStatus) {
    const memoria = $dailyStore.get(codigo);

    if (!memoria || memoria.historico.length === 0) {
      return { acao: 'CRIAR' };
    }

    const { ultimoStatus } = memoria;

    if (ultimoStatus === 'NORMALIZADO' && novoStatus === 'PENDENTE') {
      return { acao: 'RETIFICAR' };
    }
    
    if (ultimoStatus === 'PENDENTE' && novoStatus === 'NORMALIZADO') {
      return { acao: 'NORMALIZAR' };
    }

    return { acao: 'ATUALIZAR' };
  }

  function handleProcessar() {
    if (!relatorio.trim()) return;
    alertasToast = [];
    const linhas = relatorio.split('\n').map(l => l.trim()).filter(Boolean);
    let secao = 'HEADER';

    for (const linha of linhas) {
        if (linha.includes('NORMALIZADOS✅')) { secao = 'NORMALIZADO'; continue; }
        if (linha.includes('PENDENTES❌')) { secao = 'PENDENTE'; continue; }
        if (secao === 'HEADER') continue;

        const pattern = /([A-ZÀ-Ú\s]+?|)?\s*🚦\s*(\d+[T]?)\s*🚦\s*(.*?)\s*●\s*(APAGADO|PISCANTE|TRAVADO|INTERMITENTE)/i;
        const match = linha.match(pattern);

        if (match) {
            const [, causaRaw, codigo, endereco, problema] = match;
            const dados = {
                codigo: codigo.trim(), statusObservado: secao, causa: (causaRaw || 'INVESTIGANDO').trim(),
                endereco: endereco.trim(), problema: problema.toUpperCase(),
                inicio: (linha.match(/Início:\s*([\d/]+\s*[\d:]*)/i) || [])[1]?.trim() || null,
            };

            const analise = analisarOcorrencia(dados.codigo, dados.statusObservado);
            
            registrarEvento(dados.codigo, dados.statusObservado);

            cardsVisuais.update(mapa => {
                const disposition = calcularDisposition(dados);
                switch(analise.acao) {
                    case 'CRIAR':
                        if (dados.statusObservado === 'PENDENTE') {
                            mapa.set(dados.codigo, { ...dados, status: 'PENDENTE', disposition });
                        }
                        break;
                    case 'RETIFICAR':
                        mapa.set(dados.codigo, { ...dados, status: 'PENDENTE', disposition });
                        alertasToast = [...alertasToast, `↩️ **Retificação:** Ocorrência **${dados.codigo}** retornou para Pendentes.`];
                        break;
                    case 'NORMALIZAR':
                        mapa.delete(dados.codigo);
                        alertasToast = [...alertasToast, `✅ **Normalizado:** Ocorrência **${dados.codigo}** foi resolvida.`];
                        break;
                    case 'ATUALIZAR':
                        if (mapa.has(dados.codigo)) {
                            mapa.set(dados.codigo, { ...mapa.get(dados.codigo), ...dados, disposition });
                        }
                        break;
                }
                return mapa;
            });
        }
    }
    relatorio = '';
    if (alertasToast.length === 0) {
        alertasToast = ['✅ Relatório processado sem ações notáveis.'];
    }
  }

  // --- FUNÇÕES AUXILIARES E DERIVADAS ---
  function calcularPlacar(mapaPendentes, mapaHistorico) {
    const pendentes = mapaPendentes.size;
    const normalizados = mapaHistorico.size - pendentes;
    return {
      total: mapaHistorico.size,
      pendentes,
      normalizados: Math.max(0, normalizados),
    };
  }

  $: placar = calcularPlacar($cardsVisuais, $dailyStore);

  // LÓGICA VISUAL CORRIGIDA (MARK LVII)
  $: colunasVisuais = (() => {
    const cols = {
      aguardando: { id: 'aguardando', titulo: 'Aguardando Atendimento', cards: [] },
      semNecessidade: { id: 'semNecessidade', titulo: 'Sem Necessidade', cards: [] },
    };
    if ($cardsVisuais && $cardsVisuais.size > 0) {
      for (const card of $cardsVisuais.values()) {
        const colId = card.disposition === 'SEM_NECESSIDADE' ? 'semNecessidade' : 'aguardando';
        if (cols[colId]) {
          cols[colId].cards.push(card);
        }
      }
    }
    return Object.values(cols);
  })();

  function limparTudo() {
    if (window.confirm('⚠️ Limpar TODO o estado do turno? Esta ação não pode ser desfeita.')) {
        dailyStore.set(new Map());
        cardsVisuais.set(new Map());
        alertasToast = [];
    }
  }
</script>

<main>
  <!-- HEADER -->
  <div class="header">
    <div class="header-left">
      <h1 class="main-title">Memória de Aço <span class="version-badge">v57</span></h1>
    </div>
    <div class="header-right">
      <div class="placar-container">
        <div class="placar-item" title="Total de Ocorrências Únicas no Turno"><span class="placar-valor">{placar.total}</span><span class="placar-label">Total</span></div>
        <div class="placar-item" title="Pendentes Atuais"><span class="placar-valor" style="color: #f59e0b;">{placar.pendentes}</span><span class="placar-label">Pendentes</span></div>
        <div class="placar-item" title="Normalizados no Turno"><span class="placar-valor" style="color: #34d399;">{placar.normalizados}</span><span class="placar-label">Normalizados</span></div>
      </div>
      <button class="button-reset" on:click={limparTudo}>Limpar Turno</button>
    </div>
  </div>

  <!-- AVISOS (TOASTS) -->
  {#if alertasToast.length > 0}
    <div class="alert-container">
      {#each alertasToast as alerta}
        <div class="alert-message" role="alert">{@html alerta}</div>
      {/each}
    </div>
  {/if}

  <!-- GRID PRINCIPAL -->
  <div class="grid-container">
    <div class="panel input-panel">
      <h2 class="panel-title">Entrada de Relatório</h2>
      <textarea class="textarea" placeholder="Cole o relatório bruto aqui..." bind:value={relatorio}></textarea>
      <button class="button" on:click={handleProcessar}>Processar</button>
    </div>
    <div class="kanban-board-wrapper">
      <div class="kanban-board">
        {#each colunasVisuais as column (column.id)}
          <div class="kanban-column">
            <h3 class="column-title">{column.titulo} ({column.cards.length})</h3>
            <div class="cards-container">
              {#each column.cards as card (card.codigo)}
                <div class="card">
                  <div class="card-header">
                    <h4 class="card-code">{card.codigo}</h4>
                    <span class="card-problem">{card.problema}</span>
                  </div>
                  <p class="card-address">{card.endereco}</p>
                  <div class="card-footer"><span>Início: {card.inicio || 'N/A'}</span></div>
                </div>
              {/each}
            </div>
          </div>
        {/each}
      </div>
    </div>
  </div>
</main>

<style>
  :global(body) { background-color: #111827; color: #d1d5db; font-family: 'Inter', sans-serif; margin: 0; }
  main { padding: 1.5rem 2rem; height: 100vh; box-sizing: border-box; display: flex; flex-direction: column; }
  .version-badge { font-size: 0.7rem; font-weight: 700; padding: 0.1rem 0.4rem; border-radius: 0.5rem; background-color: #4f46e5; color: #e0e7ff; vertical-align: super; margin-left: 0.25rem; }
  .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; flex-shrink: 0; }
  .main-title { font-size: 1.75rem; font-weight: 700; color: #f9fafb; }
  .placar-container { display: flex; align-items: center; gap: 0.5rem; background-color: #1f2937; padding: 0.25rem 0.75rem; border-radius: 0.5rem; border: 1px solid #374151; }
  .placar-item { display: flex; align-items: baseline; gap: 0.4rem; padding: 0.25rem 0.75rem; border-right: 1px solid #4b5563; }
  .placar-item:last-child { border-right: none; }
  .placar-valor { font-size: 1.25rem; font-weight: 700; color: #f9fafb; }
  .placar-label { font-size: 0.75rem; font-weight: 500; color: #9ca3af; text-transform: uppercase; }
  .button-reset { background-color: #991b1b; color: white; font-weight: 600; padding: 0.6rem 1rem; border-radius: 0.5rem; border: none; cursor: pointer; }
  .button-reset:hover { background-color: #7f1d1d; }
  .alert-container { padding: 0.75rem 1rem; background-color: #1e40af; color: #e0e7ff; border-radius: 0.5rem; margin-bottom: 1rem; font-size: 0.9rem; }
  .grid-container { display: grid; grid-template-columns: 350px 1fr; gap: 1.5rem; flex-grow: 1; min-height: 0; }
  .panel.input-panel { background-color: #1f2937; border: 1px solid #374151; padding: 1.5rem; border-radius: 0.75rem; display: flex; flex-direction: column; height: fit-content; max-height: 550px; }
  .panel-title { font-weight: 600; font-size: 1.25rem; margin-bottom: 1rem; color: #e5e7eb; }
  .textarea { width: 100%; height: 350px; padding: 0.75rem; border: 1px solid #4b5563; border-radius: 0.5rem; resize: none; font-family: monospace; background-color: #374151; color: #d1d5db; }
  .button { width: 100%; background-color: #3b82f6; color: white; font-weight: 700; padding: 0.75rem 1rem; border-radius: 0.5rem; margin-top: 1rem; border: none; cursor: pointer; }
  .button:hover { background-color: #2563eb; }
  .kanban-board-wrapper { min-width: 0; }
  .kanban-board { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(320px, 1fr); gap: 1rem; height: 100%; overflow-x: auto; padding-bottom: 1rem; }
  .kanban-column { background-color: #1f2937; border-radius: 0.5rem; padding: 0.75rem; display: flex; flex-direction: column; height: 100%; }
  .column-title { font-weight: 700; margin: 0 0 1rem 0; padding: 0 0.25rem; color: #e5e7eb; }
  .cards-container { flex-grow: 1; overflow-y: auto; padding: 0.25rem; display: flex; flex-direction: column; gap: 0.75rem; }
  .card { background-color: #374151; border-radius: 0.5rem; padding: 1rem; border-left: 4px solid #6b7280; }
  .card-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.5rem; }
  .card-code { font-weight: 700; font-size: 1.125rem; color: #f9fafb; }
  .card-problem { font-size: 0.8rem; font-weight: 600; color: #fcd34d; }
  .card-address { color: #d1d5db; font-size: 0.875rem; }
  .card-footer { margin-top: 0.75rem; font-size: 0.75rem; color: #9ca3af; }
</style>
