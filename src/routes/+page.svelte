<!-- MARK LXVII: PROTOCOLO O PAINEL CINÉTICO -->
<script>
  import { writable } from 'svelte/store';
  import { onMount } from 'svelte';

  // --- STORES E ESTADO ---
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

  const dailyStore = createPersistentStore('daily-store-v67', new Map());
  const cardsVisuais = createPersistentStore('cards-visuais-v67', new Map());

  let relatorioBruto = '';
  let relatorioGerado = '';
  let alertasToast = [];
  let dataAtual = '';
  let abaAtiva = 'entrada';

  onMount(() => {
    const hoje = new Date();
    const dia = String(hoje.getDate()).padStart(2, '0');
    const mes = String(hoje.getMonth() + 1).padStart(2, '0');
    const ano = String(hoje.getFullYear()).slice(-2);
    dataAtual = `${dia}.${mes}.${ano}`;
  });

  // --- LÓGICA DO TRONCO: PROCESSAMENTO (SEM ALTERAÇÕES) ---
  function handleProcessar() {
    if (!relatorioBruto.trim()) return;
    alertasToast = [];
    const linhas = relatorioBruto.split('\n').map(l => l.trim()).filter(Boolean);
    let secao = 'HEADER';

    for (const linha of linhas) {
        if (linha.includes('NORMALIZADOS')) { secao = 'NORMALIZADO'; continue; }
        if (linha.includes('PENDENTES')) { secao = 'PENDENTE'; continue; }
        if (secao === 'HEADER') continue;

        const pattern = /([A-ZÀ-Ú\s]+?|)?\s*🚦\s*(\d+[T]?)\s*🚦\s*(.*?)\s*●\s*(APAGADO|PISCANTE|TRAVADO|INTERMITENTE|N\/I)/i;
        const match = linha.match(pattern);

        if (match) {
            const [, causaRaw, codigo, endereco, problema] = match;
            const dados = {
                codigo: codigo.trim(), statusObservado: secao, causa: (causaRaw || 'INVESTIGANDO').trim(),
                endereco: endereco.trim(), problema: problema.trim().toUpperCase(),
                inicio: (linha.match(/Início:\s*([\d/]+\s*[\d:]*)/i) || [])[1]?.trim() || null,
                id: self.crypto.randomUUID(),
            };

            const memoria = $dailyStore.get(dados.codigo);
            const acao = (!memoria || memoria.historico.length === 0) ? 'CRIAR'
              : (memoria.ultimoStatus === 'NORMALIZADO' && dados.statusObservado === 'PENDENTE') ? 'RETIFICAR'
              : (memoria.ultimoStatus === 'PENDENTE' && dados.statusObservado === 'NORMALIZADO') ? 'NORMALIZAR'
              : 'ATUALIZAR';

            dailyStore.update(mapa => {
                if (!mapa.has(dados.codigo)) mapa.set(dados.codigo, { historico: [] });
                const item = mapa.get(dados.codigo);
                item.historico.push({ status: dados.statusObservado, timestamp: new Date().toISOString() });
                item.ultimoStatus = dados.statusObservado;
                return mapa;
            });

            cardsVisuais.update(mapa => {
                let disposition = (dados.endereco || '').toLowerCase().includes('pgv') ? 'SEM_NECESSIDADE' : 'AGUARDANDO';
                
                switch(acao) {
                    case 'CRIAR':
                        if (dados.statusObservado === 'PENDENTE') mapa.set(dados.codigo, { ...dados, disposition });
                        break;
                    case 'RETIFICAR':
                        mapa.set(dados.codigo, { ...dados, disposition });
                        alertasToast = [...alertasToast, `↩️ **Retificação:** ${dados.codigo} retornou.`];
                        break;
                    case 'NORMALIZAR':
                        mapa.delete(dados.codigo);
                        alertasToast = [...alertasToast, `✅ **Normalizado:** ${dados.codigo} resolvido.`];
                        break;
                    case 'ATUALIZAR':
                        if (mapa.has(dados.codigo)) mapa.set(dados.codigo, { ...mapa.get(dados.codigo), ...dados, disposition });
                        break;
                }
                return mapa;
            });
        }
    }
    relatorioBruto = '';
    if (alertasToast.length === 0) alertasToast = ['✅ Relatório processado sem ações notáveis.'];
  }

  // --- LÓGICA DA RAMIFICAÇÃO: GERAÇÃO DE RELATÓRIO (SEM ALTERAÇÕES) ---
  function handleGerarRelatorio() {
    let relatorio = `RELATÓRIO DE ATENDIMENTO SEMAFÓRICO – ${dataAtual}\n\n`;
    const todasOcorrencias = Array.from($cardsVisuais.values());
    const normalizadosCount = $dailyStore.size - $cardsVisuais.size;
    relatorio += `Ocorrências de Apagados/Piscantes (${$cardsVisuais.size})\n`;
    relatorio += `Pendentes (${$cardsVisuais.size})\n`;
    relatorio += `Normalizados (${Math.max(0, normalizadosCount)})\n\n`;
    const formatarLinha = (card) => `${card.codigo} 🚦 ${card.endereco} ● ${card.problema}`;
    const viaLivre = todasOcorrencias.filter(c => c.disposition === 'VIA_LIVRE');
    if (viaLivre.length > 0) {
      relatorio += 'ATENDIDOS PELO VIA LIVRE (EM ANDAMENTO):\n';
      viaLivre.forEach(c => relatorio += `${formatarLinha(c)}\n`);
      relatorio += '\n';
    }
    const amc = todasOcorrencias.filter(c => c.disposition === 'AMC');
    if (amc.length > 0) {
      relatorio += 'ATENDIDOS PELA AMC (EM ANDAMENTO):\n';
      amc.forEach(c => relatorio += `${formatarLinha(c)}\n`);
      relatorio += '\n';
    }
    const aguardando = todasOcorrencias.filter(c => c.disposition === 'AGUARDANDO');
    if (aguardando.length > 0) {
      relatorio += 'PENDENTES / OUTROS MOTIVOS:\n';
      aguardando.forEach(c => relatorio += `${formatarLinha(c)}\n`);
      relatorio += '\n';
    }
    const semNecessidade = todasOcorrencias.filter(c => c.disposition === 'SEM_NECESSIDADE');
    if (semNecessidade.length > 0) {
      relatorio += '- - Sem necessidade de operação:\n';
      semNecessidade.forEach(c => relatorio += `${formatarLinha(c)}\n`);
      relatorio += '\n';
    }
    relatorioGerado = relatorio.trim();
    abaAtiva = 'saida';
    alertasToast = ['📋 Relatório gerado e pronto para cópia.'];
  }

  function copiarRelatorio() {
    if (!relatorioGerado) return;
    navigator.clipboard.writeText(relatorioGerado).then(() => {
      alertasToast = ['✅ Relatório copiado para a área de transferência!'];
    }, () => {
      alertasToast = ['❌ Falha ao copiar o relatório.'];
    });
  }

  // --- LÓGICA DE INTERATIVIDADE (NOVO - MARK LXVII) ---
  function handleDragStart(event, cardCodigo) {
    event.dataTransfer.setData('text/plain', cardCodigo);
    event.dataTransfer.effectAllowed = 'move';
  }

  function handleDragOver(event) {
    event.preventDefault(); // Essencial para permitir o drop
    event.dataTransfer.dropEffect = 'move';
  }

  function handleDrop(event, colunaDestinoId) {
    event.preventDefault();
    const cardCodigo = event.dataTransfer.getData('text/plain');
    
    cardsVisuais.update(mapa => {
      const card = mapa.get(cardCodigo);
      if (card && card.disposition !== colunaDestinoId) {
        card.disposition = colunaDestinoId;
        mapa.set(cardCodigo, card);
        alertasToast = [`🔄 Card ${cardCodigo} movido para ${colunaDestinoId.replace('_', ' ')}.`];
      }
      return mapa;
    });
  }

  // --- LÓGICA VISUAL ---
  $: colunasVisuais = (() => {
    const cols = {
      AGUARDANDO: { id: 'AGUARDANDO', titulo: 'Aguardando', cards: [] },
      VIA_LIVRE: { id: 'VIA_LIVRE', titulo: 'Via Livre', cards: [] },
      AMC: { id: 'AMC', titulo: 'AMC', cards: [] },
      SEM_NECESSIDADE: { id: 'SEM_NECESSIDADE', titulo: 'Sem Necessidade', cards: [] },
    };
    for (const card of $cardsVisuais.values()) {
        if (cols[card.disposition]) {
          cols[card.disposition].cards.push(card);
        }
    }
    return Object.values(cols);
  })();

  function limparTudo() {
    if (window.confirm('⚠️ Limpar TODO o estado do turno? Esta ação não pode ser desfeita.')) {
        dailyStore.set(new Map());
        cardsVisuais.set(new Map());
        relatorioGerado = '';
        alertasToast = [];
        abaAtiva = 'entrada';
    }
  }
</script>

<main>
  <!-- HEADER -->
  <div class="header">
    <h1 class="main-title">Memória de Aço <span class="version-badge">v67</span></h1>
    <button class="button-generate" on:click={handleGerarRelatorio}>Gerar Relatório de Saída</button>
    <button class="button-reset" on:click={limparTudo}>Limpar Turno</button>
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
    <!-- PAINEL DE ENTRADA/SAÍDA COM ABAS -->
    <div class="panel io-panel">
      <div class="tabs">
        <button class="tab-button" class:active={abaAtiva === 'entrada'} on:click={() => abaAtiva = 'entrada'}>Entrada de Dados</button>
        <button class="tab-button" class:active={abaAtiva === 'saida'} on:click={() => abaAtiva = 'saida'}>Relatório de Saída</button>
      </div>

      {#if abaAtiva === 'entrada'}
        <div class="tab-content">
          <textarea class="textarea" placeholder="Cole o relatório bruto aqui..." bind:value={relatorioBruto} spellcheck="false"></textarea>
          <button class="button" on:click={handleProcessar}>Processar Relatório Bruto</button>
        </div>
      {/if}

      {#if abaAtiva === 'saida'}
        <div class="tab-content">
          {#if relatorioGerado}
            <textarea class="textarea" readonly>{relatorioGerado}</textarea>
            <button class="button" on:click={copiarRelatorio}>Copiar Relatório</button>
          {:else}
            <div class="placeholder-saida">
              <p>O relatório de saída aparecerá aqui.</p>
              <p>Clique em "Gerar Relatório de Saída" no cabeçalho para começar.</p>
            </div>
          {/if}
        </div>
      {/if}
    </div>

    <!-- PAINEL KANBAN INTERATIVO (MARK LXVII) -->
    <div class="kanban-board-wrapper">
      <div class="kanban-board">
        {#each colunasVisuais as column (column.id)}
          <div 
            class="kanban-column"
            role="list"
            on:dragover={handleDragOver}
            on:drop={(event) => handleDrop(event, column.id)}
>
            <h3 class="column-title">{column.titulo} ({column.cards.length})</h3>
            <div class="cards-container">
              {#each column.cards as card (card.id)}
                <div 
                  class="card"
                  draggable="true"
                  on:dragstart={(event) => handleDragStart(event, card.codigo)}
                >
                  <div class="card-header"><h4 class="card-code">{card.codigo}</h4><span class="card-problem">{card.problema}</span></div>
                  <p class="card-address">{card.endereco}</p>
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
  :global(body) { background-color: #111827; color: #d1d5db; font-family: 'Inter', sans-serif; margin: 0; overflow: hidden; }
  main { padding: 1.5rem 2rem; height: 100vh; box-sizing: border-box; display: flex; flex-direction: column; }
  .version-badge { font-size: 0.7rem; font-weight: 700; padding: 0.1rem 0.4rem; border-radius: 0.5rem; background-color: #4f46e5; color: #e0e7ff; vertical-align: super; margin-left: 0.25rem; }
  
  .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; flex-shrink: 0; }
  .main-title { font-size: 1.75rem; font-weight: 700; color: #f9fafb; }
  .button-generate { background-color: #16a34a; color: white; font-weight: 600; padding: 0.6rem 1rem; border-radius: 0.5rem; border: none; cursor: pointer; }
  .button-generate:hover { background-color: #15803d; }
  .button-reset { background-color: #991b1b; color: white; font-weight: 600; padding: 0.6rem 1rem; border-radius: 0.5rem; border: none; cursor: pointer; }
  .button-reset:hover { background-color: #7f1d1d; }
  
  .alert-container { padding: 0.75rem 1rem; background-color: #1e40af; color: #e0e7ff; border-radius: 0.5rem; margin-bottom: 1rem; font-size: 0.9rem; flex-shrink: 0; }
  .grid-container { display: grid; grid-template-columns: 380px 1fr; gap: 1.5rem; flex-grow: 1; min-height: 0; }
  
  .panel.io-panel { background-color: #1f2937; border: 1px solid #374151; border-radius: 0.75rem; display: flex; flex-direction: column; }
  .tabs { display: flex; border-bottom: 1px solid #374151; }
  .tab-button { flex: 1; background: none; border: none; color: #9ca3af; padding: 0.75rem 1rem; cursor: pointer; font-size: 1rem; font-weight: 500; }
  .tab-button.active { color: #f9fafb; background-color: #374151; border-top-left-radius: 0.75rem; border-top-right-radius: 0.75rem; }
  .tab-content { padding: 1.5rem; display: flex; flex-direction: column; flex-grow: 1; gap: 1rem; }
  
  .textarea { width: 100%; flex-grow: 1; padding: 0.75rem; border: 1px solid #4b5563; border-radius: 0.5rem; resize: none; font-family: monospace; background-color: #374151; color: #d1d5db; outline: none; }
  .textarea:focus { border-color: #3b82f6; }
  .button { width: 100%; background-color: #3b82f6; color: white; font-weight: 700; padding: 0.75rem 1rem; border-radius: 0.5rem; border: none; cursor: pointer; }
  .button:hover { background-color: #2563eb; }
  
  .placeholder-saida { flex-grow: 1; display: flex; flex-direction: column; justify-content: center; align-items: center; text-align: center; color: #6b7280; }

  .kanban-board-wrapper { min-width: 0; overflow-x: auto; }
  .kanban-board { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(320px, 1fr); gap: 1rem; height: 100%; }
  .kanban-column { background-color: #1f2937; border-radius: 0.5rem; padding: 0.75rem; display: flex; flex-direction: column; height: 100%; transition: background-color 0.2s ease; }
  .kanban-column:has(.drag-over) { background-color: #374151; } /* Feedback visual ao arrastar sobre a coluna */
  .column-title { font-weight: 700; margin: 0 0 1rem 0; padding: 0 0.25rem; color: #e5e7eb; flex-shrink: 0; }
  .cards-container { flex-grow: 1; overflow-y: auto; padding: 0.25rem; display: flex; flex-direction: column; gap: 0.75rem; }
  
  .card { background-color: #374151; border-radius: 0.5rem; padding: 1rem; border-left: 4px solid #6b7280; cursor: grab; }
  .card:active { cursor: grabbing; }
  .card.dragging { opacity: 0.5; } /* Feedback visual para o card sendo arrastado */

  .card-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.5rem; }
  .card-code { font-weight: 700; font-size: 1.125rem; color: #f9fafb; }
  .card-problem { font-size: 0.8rem; font-weight: 600; color: #fcd34d; }
  .card-address { color: #d1d5db; font-size: 0.875rem; }
</style>
