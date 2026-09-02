
// ================================================================ 全局状态
const state = {
  data: null,
  direction: null,
  activeDir: null,     // 全局当前方向（刷题/面试/报告共享）
  sessionId: null,
  chart: null,
  userState: { mastered: [], bookmarks: [], last_direction: null },
};
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

// ================================================================ 学习状态
async function loadUserState() {
  try {
    state.userState = await apiGet('/api/state');
  } catch (e) {
    state.userState = { mastered: [], bookmarks: [], last_direction: null };
  }
}
async function toggleMastered(qid) {
  try {
    const r = await apiPost('/api/state/mastered', { question_id: qid });
    state.userState.mastered = r.mastered;
    renderBankQuestions();
    renderDirList();
    toast(r.action === 'added' ? '已标记为掌握' : '已取消掌握标记');
  } catch (e) { toast('操作失败：' + e.message); }
}

// 结果页 / 复盘页的标记掌握：即时更新按钮，同时同步题库侧栏进度
async function toggleMasteredQuick(qid, btn) {
  try {
    const r = await apiPost('/api/state/mastered', { question_id: qid });
    state.userState.mastered = r.mastered;
    if (btn) {
      const on = r.mastered.includes(qid);
      const label = btn.querySelector('.rv-btn-label');
      if (label) label.textContent = on ? '已掌握' : '标记掌握';
      else btn.textContent = on ? '已掌握' : '标记掌握';
      btn.classList.toggle('on', on);
    }
    renderBankQuestions();
    renderDirList();
    toast(r.action === 'added' ? '已标记为掌握' : '已取消掌握标记');
  } catch (e) { toast('操作失败：' + e.message); }
}
async function toggleBookmark(qid) {
  try {
    const r = await apiPost('/api/state/bookmarks', { question_id: qid });
    state.userState.bookmarks = r.bookmarks;
    renderBankQuestions();
    toast(r.action === 'added' ? '已收藏' : '已取消收藏');
  } catch (e) { toast('操作失败：' + e.message); }
}

// ================================================================ 工具
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg; t.style.display = 'block';
  clearTimeout(t._h); t._h = setTimeout(() => t.style.display = 'none', 2600);
}
async function apiGet(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
async function apiPost(url, body) {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// 模态框打开时锁定页面滚动，防止背景跟随滚动（滚动穿透）。
// 用计数支持模态框叠加（如确认框叠在追问框上），关闭最内层不误解除外层锁定。
let bodyLockCount = 0;
function lockBodyScroll(lock) {
  if (lock) bodyLockCount++;
  else bodyLockCount = Math.max(0, bodyLockCount - 1);
  document.body.style.overflow = bodyLockCount > 0 ? 'hidden' : '';
}

// 方向视觉标识（无 emoji：色块 + 编号）
const DIR_CHIP = { linux: 'chip-indigo', network: 'chip-teal', database: 'chip-orange', cloud: 'chip-violet', cicd: 'chip-red', sre: 'chip-green', ai: 'chip-cyan' };
const DIR_NO = { linux: '01', network: '02', database: '03', cloud: '04', cicd: '05', sre: '06', ai: '07' };
const DIR_SHORT = { linux: 'OS', network: '网', database: '库', cloud: '云', cicd: 'CD', sre: '稳', ai: 'AI' };

// ESC 关闭模态框
const findModal = document.getElementById('find-modal');
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  // 追问是多实例弹窗：ESC 只关最顶层显示中的那个
  const top = [...fuInstances].reverse().find(x => x.overlay && x.overlay.classList.contains('show'));
  if (top) { closeFollowupModal(top); return; }
  closeFindModal();
});
findModal.addEventListener('click', (e) => { if (e.target === findModal) closeFindModal(); });

// ================================================================ 联网找新题
let findDirs = [];     // 多选方向
let findCount = 6;     // 每方向搜题数量
let findCands = [];    // 当前候选题列表
const FIND_BTN_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>';

function openFindModal() {
  lockBodyScroll(true);
  findDirs = state.activeDir ? [state.activeDir] : [];
  renderFindDirs();
  // 重置结果区
  document.getElementById('find-results').innerHTML = '';
  document.getElementById('find-actions').style.display = 'none';
  document.getElementById('find-status').textContent = '';
  document.getElementById('find-modal').classList.add('show');
}

// 渲染方向多选 chips + 新建方向按钮
function renderFindDirs() {
  const box = document.getElementById('find-dirs');
  if (!box) return;
  const dirs = state.data?.directions || [];
  const qs = state.data?.questions || [];
  const cntOf = {};
  qs.forEach(q => { cntOf[q.direction] = (cntOf[q.direction] || 0) + 1; });
  box.innerHTML = dirs.map(d => {
    const n = cntOf[d.id] || 0;
    return `<span class="fdir ${findDirs.includes(d.id) ? 'on' : ''}" onclick="toggleFindDir('${d.id}')">${esc(d.name)}<span class="fdir-cnt">${n}</span></span>`;
  }).join('') + `<button class="fdir-add" onclick="openAddDirFromFind()">＋ 新建</button>`;
  updateFindDirCount();
}

function toggleFindDir(id) {
  const i = findDirs.indexOf(id);
  if (i >= 0) findDirs.splice(i, 1);
  else findDirs.push(id);
  renderFindDirs();
}

function updateFindDirCount() {
  const el = document.getElementById('find-dir-count');
  if (!el) return;
  el.textContent = findDirs.length ? `已选 ${findDirs.length} 个方向` : '';
}

function closeFindModal() {
  lockBodyScroll(false);
  document.getElementById('find-modal').classList.remove('show');
}

function pickCount(n) {
  findCount = n;
  document.querySelectorAll('#find-count .count-opt').forEach(o => o.classList.toggle('sel', +o.dataset.v === n));
  const c = document.getElementById('find-count-custom');
  if (c) c.value = '';
}
// 自定义搜题数：输入时选中，回车/失焦生效
(function () {
  const c = document.getElementById('find-count-custom');
  if (!c) return;
  const apply = () => {
    const v = c.value.trim();
    if (!v) return;  // 没输入不提示
    const n = parseInt(v, 10);
    if (!n || n < 1 || n > 30) {
      toast('搜题数需在 1~30 之间');
      c.value = '';
      document.querySelectorAll('#find-count .count-opt').forEach(o => o.classList.remove('sel'));
      return;
    }
    findCount = n;
    document.querySelectorAll('#find-count .count-opt').forEach(o => o.classList.remove('sel'));
  };
  c.addEventListener('input', apply);
  c.addEventListener('blur', apply);
})();

async function runFind() {
  if (!findDirs.length) { toast('先选至少一个方向'); return; }
  const btn = document.getElementById('find-run-btn');
  const status = document.getElementById('find-status');
  btn.disabled = true;
  btn.textContent = '搜索中…';
  status.textContent = `正在联网搜索真实面经并让 AI 提炼（${findDirs.length} 个方向，约 20-60 秒/方向）…`;
  try {
    const r = await apiPost('/api/find-questions', { directions: findDirs, count: findCount });
    findCands = r.candidates || [];
    const box = document.getElementById('find-results');
    status.textContent = r.message || '';
    if (!findCands.length) {
      box.innerHTML = '<div class="empty" style="padding:20px">没有提炼出新题（可能都已在题库或搜索失败）</div>';
      document.getElementById('find-actions').style.display = 'none';
      return;
    }
    box.innerHTML = findCands.map((c, i) => {
      const q = c.question || '';
      const ans = (c.answer || []).join('；');
      const dname = (state.data?.directions.find(dd => dd.id === c.direction) || {}).name;
      const meta = [
        c.importance ? `<span class="badge ${c.importance === '高频' ? 'high' : 'mid'}">${esc(c.importance)}</span>` : '',
        c.difficulty ? `<span class="badge ${esc(c.difficulty)}">${esc(c.difficulty)}</span>` : '',
        c.topic ? `<span class="badge" style="color:var(--accent2);background:var(--accent2-soft);border-color:transparent">${esc(c.topic)}</span>` : '',
        dname ? `<span class="badge fdir-badge">${esc(dname)}</span>` : '',
      ].join('');
      return `<div class="find-item">
        <label class="f-head">
          <input type="checkbox" data-i="${i}" checked onchange="updateImportBtn()">
          <div class="f-body">
            <div class="f-meta">${meta}</div>
            <div class="f-q">${esc(q)}</div>
            ${ans ? `<div class="f-ans"><b>要点：</b>${esc(ans)}</div>` : ''}
          </div>
        </label>
      </div>`;
    }).join('');
    document.getElementById('find-actions').style.display = 'flex';
    updateImportBtn();
  } catch (e) {
    status.textContent = '搜索失败：' + e.message;
    toast('搜索失败：' + e.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = FIND_BTN_ICON + ' 开始搜索';
  }
}

function toggleAll(on) {
  document.querySelectorAll('#find-results input[type=checkbox]').forEach(c => { c.checked = on; });
  updateImportBtn();
}

function updateImportBtn() {
  const all = document.querySelectorAll('#find-results input[type=checkbox]').length;
  const n = document.querySelectorAll('#find-results input[type=checkbox]:checked').length;
  document.getElementById('find-import-btn').textContent = n ? `导入选中（${n}）` : '导入选中';
  document.getElementById('find-sel-label').textContent = `已选 ${n} / ${all}`;
}

async function importSelected() {
  const checked = [...document.querySelectorAll('#find-results input[type=checkbox]:checked')].map(c => parseInt(c.dataset.i, 10));
  if (!checked.length) { toast('先勾选要导入的题'); return; }
  const sel = checked.map(i => findCands[i]);
  const btn = document.getElementById('find-import-btn');
  btn.disabled = true;
  btn.textContent = '导入中…';
  try {
    // 按来源方向分组导入
    const groups = {};
    for (const q of sel) {
      const d = q.direction || findDirs[0];
      (groups[d] = groups[d] || []).push(q);
    }
    let total = 0;
    for (const [d, qs] of Object.entries(groups)) {
      const r = await apiPost('/api/import-questions', { direction: d, questions: qs });
      total += r.imported || qs.length;
    }
    toast(`已导入 ${total} 题`);
    closeFindModal();
    // 刷新题库（含新题）
    state.data = await apiGet('/api/questions');
    renderDirList();
    renderBankQuestions();
    renderInterviewEntry();
  } catch (e) {
    toast('导入失败：' + e.message);
    btn.disabled = false;
    btn.textContent = '导入选中';
  }
}

// ================================================================ 新建方向（智能拆解）
const DIR_COLOR_POOL = ['indigo', 'teal', 'orange', 'violet', 'red', 'green', 'cyan'];
let adirCands = [];

function openAddDirModal() {
  lockBodyScroll(true);
  document.getElementById('adir-name').value = '';
  document.getElementById('adir-status').textContent = '';
  document.getElementById('adir-step2').style.display = 'none';
  document.getElementById('adir-step1').style.display = '';
  document.getElementById('add-dir-modal').classList.add('show');
  setTimeout(() => { const i = document.getElementById('adir-name'); if (i) i.focus(); }, 80);
}

function closeAddDirModal() {
  lockBodyScroll(false);
  document.getElementById('add-dir-modal').classList.remove('show');
}

// 直接创建单个方向（用户已有明确分类，不经 AI 拆解）
async function createSingleDir() {
  const name = document.getElementById('adir-name').value.trim();
  if (!name) { toast('先输入方向名称'); return; }
  const btn = document.getElementById('adir-create-single');
  btn.disabled = true; btn.textContent = '创建中…';
  try {
    const r = await apiPost('/api/directions', { action: 'add', name });
    toast(r.message || '已创建');
    closeAddDirModal();
    state.data = await apiGet('/api/questions');
    renderDirList();
    renderBankQuestions();
    renderInterviewEntry();
    refreshTrashBadge();
    if (findReturnAfterAdd) {
      findReturnAfterAdd = false;
      const d = r.direction;
      if (d && d.id && !findDirs.includes(d.id)) findDirs.push(d.id);
      renderFindDirs();
    }
  } catch (e) {
    toast('创建失败：' + e.message);
    btn.disabled = false; btn.textContent = '直接创建';
  }
}

async function expandDirs() {
  const name = document.getElementById('adir-name').value.trim();
  if (!name) { toast('先输入领域名'); return; }
  const btn = document.getElementById('adir-expand-btn');  const status = document.getElementById('adir-status');
  btn.disabled = true; btn.textContent = '拆解中…';
  status.textContent = '正在拆解「' + name + '」的子方向…（约 10-30 秒）';
  try {
    const r = await apiPost('/api/directions/expand', { name });
    adirCands = r.candidates || [];
    status.textContent = r.message || '';
    if (!adirCands.length) {
      status.textContent = '没有拆出新的子方向（可能都已存在）。可以换个更细的领域名试试。';
      return;
    }
    document.getElementById('adir-cands').innerHTML = adirCands.map((c, i) => `
      <label class="adir-item">
        <input type="checkbox" class="adir-check" data-i="${i}" checked onchange="updateAdirBtn()">
        <span class="adir-chip chip-${DIR_COLOR_POOL[i % 7]}">${esc(c.name.slice(0, 2))}</span>
        <span class="adir-body">
          <span class="adir-name">${esc(c.name)}</span>
          <span class="adir-desc">${esc(c.desc)}</span>
          <span class="adir-kw">搜题：${esc(c.keyword)}</span>
        </span>
      </label>`).join('');
    document.getElementById('adir-step2').style.display = '';
    updateAdirBtn();
  } catch (e) {
    status.textContent = '拆解失败：' + e.message;
    toast('拆解失败：' + e.message);
  } finally {
    btn.disabled = false; btn.textContent = '智能拆解';
  }
}
// 输入框 Enter 直接触发智能拆解（主操作）
(function () {
  const inp = document.getElementById('adir-name');
  if (inp) inp.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); expandDirs(); }
  });
})();

function updateAdirBtn() {
  const n = document.querySelectorAll('.adir-check:checked').length;
  document.getElementById('adir-create-btn').textContent = n ? `创建勾选的方向（${n}）` : '创建勾选的方向';
}

async function createDirs() {
  const sel = [...document.querySelectorAll('.adir-check:checked')].map(c => adirCands[+c.dataset.i]);
  if (!sel.length) { toast('至少勾选一个子方向'); return; }
  const btn = document.getElementById('adir-create-btn');
  btn.disabled = true; btn.textContent = '创建中…';
  try {
    const r = await apiPost('/api/directions/batch-add', { directions: sel });
    toast(r.message || '方向已创建');
    closeAddDirModal();
    state.data = await apiGet('/api/questions');
    renderDirList();
    renderBankQuestions();
    renderInterviewEntry();
    // 从"联网找新题"进来：建完回到找题（弹窗保持打开），保留原选择并自动勾选新建方向
    if (findReturnAfterAdd) {
      findReturnAfterAdd = false;
      const created = (r.directions || []).filter(d => d && d.id);
      created.forEach(d => { if (!findDirs.includes(d.id)) findDirs.push(d.id); });
      renderFindDirs();
    }
  } catch (e) {
    toast('创建失败：' + e.message);
    btn.disabled = false; btn.textContent = '创建勾选的方向';
  }
}

// 从"联网找新题"进入新建方向流程：不关闭找题弹窗，叠加在上层；建完保留原选择并自动勾选新建方向
let findReturnAfterAdd = false;
function openAddDirFromFind() {
  findReturnAfterAdd = true;
  openAddDirModal();
}

// ================================================================ 流式（SSE）支持
// 用 fetch + ReadableStream 解析 SSE（EventSource 不支持 POST）
async function apiStream(path, body, onEvent, signal) {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = j.error || msg; } catch (e) {}
    throw new Error(msg);
  }
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let sep;
    while ((sep = buf.indexOf('\n\n')) !== -1) {
      const frame = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      let ev = 'message', data = '';
      for (const line of frame.split('\n')) {
        if (line.startsWith('event:')) ev = line.slice(6).trim();
        else if (line.startsWith('data:')) data += line.slice(5).trim();
      }
      if (!data) continue;
      let payload = {};
      try { payload = JSON.parse(data); } catch (e) { continue; }
      onEvent(ev, payload);
    }
  }
}

// 创建一个流式气泡：思考折叠区 + 正文区
function createStreamBubble(containerId, role, thinkLabel) {
  const chatBox = document.getElementById(containerId);
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  div.innerHTML = `
    <details class="think" style="display:none">
      <summary>${esc(thinkLabel || '思考中')}</summary>
      <div class="think-body"></div>
    </details>
    <div class="say"></div>
    <div class="fu-dots"><i></i><i></i><i></i></div>`;
  chatBox.appendChild(div);
  chatBox.scrollTop = chatBox.scrollHeight;
  const think = div.querySelector('.think');
  const thinkBody = div.querySelector('.think-body');
  const say = div.querySelector('.say');
  const dots = div.querySelector('.fu-dots');
  let full = '', firstContent = true, renderPending = false;
  const scheduleRender = () => {
    if (renderPending) return;
    renderPending = true;
    requestAnimationFrame(() => {
      renderPending = false;
      say.innerHTML = renderMarkdown(full);
      chatBox.scrollTop = chatBox.scrollHeight;
    });
  };
  return {
    el: div,
    reasoning(t) {
      think.style.display = '';
      thinkBody.textContent += t;
      thinkBody.scrollTop = thinkBody.scrollHeight;
      chatBox.scrollTop = chatBox.scrollHeight;
    },
    content(t) {
      if (firstContent) { firstContent = false; dots.style.display = 'none'; }
      full += t;
      scheduleRender();
      chatBox.scrollTop = chatBox.scrollHeight;
    },
    finish(label) {
      dots.style.display = 'none';
      think.classList.add('done');
      if (label) think.querySelector('summary').textContent = label;
      chatBox.scrollTop = chatBox.scrollHeight;
    },
    setText(t) {
      dots.style.display = 'none';
      say.innerHTML = renderMarkdown(t);
      chatBox.scrollTop = chatBox.scrollHeight;
    }
  };
}

// ================================================================ TAB 切换
$$('.tab').forEach(tab => {
  tab.addEventListener('click', () => showTab(tab.dataset.tab));
});

// ================================================================ 刷题：左侧方向栏 + 题目直出
function renderDirList() {
  if (!state.data) return;
  const box = document.getElementById('bank-dir-list');
  const allCount = state.data.questions.length;
  const allMastered = state.data.questions.filter(q => state.userState.mastered.includes(q.id)).length;
  const allPct = allCount ? Math.round(allMastered / allCount * 100) : 0;
  let html = `<div class="dir-side-item ${!state.activeDir ? 'on' : ''}" onclick="setActiveDir(null)">
      <span class="dir-chip chip-all">全</span>
      <span class="dir-name">全部</span>
      <span class="dir-count">${allCount}</span>
      ${allPct > 0 ? `<span class="dir-pct">${allPct}%</span>` : ''}
    </div>`;
  html += state.data.directions.map(d => {
    const count = state.data.questions.filter(q => q.direction === d.id).length;
    const mastered = state.data.questions.filter(q => q.direction === d.id && state.userState.mastered.includes(q.id)).length;
    const pct = count ? Math.round(mastered / count * 100) : 0;
    const dot = pct === 100 ? '<span class="dir-dot done" title="已全部掌握"></span>' :
                mastered > 0 ? `<span class="dir-dot part" title="已掌握 ${mastered}/${count}"></span>` : '';
    const chipCls = DIR_CHIP[d.id] || 'chip-' + (d.color || 'indigo');
    const short = DIR_SHORT[d.id] || (d.name || '').slice(0, 2) || '？';
    return `<div class="dir-side-item ${state.activeDir === d.id ? 'on' : ''}" draggable="true" data-dir="${d.id}"
      ondragstart="dirDragStart(event)" ondragover="dirDragOver(event)" ondrop="dirDrop(event)" ondragend="dirDragEnd(event)"
      onclick="setActiveDir('${d.id}')">
      <span class="dir-grip" title="拖动排序">⠿</span>
      <span class="dir-chip ${chipCls}">${short}</span>
      <span class="dir-name">${esc(d.name)}</span>
      <span class="dir-count">${count}</span>
      ${dot}
      <span class="dir-del" title="删除方向" onclick="event.stopPropagation();askDeleteDir(event,'${d.id}','${esc(d.name)}')">×</span>
    </div>`;
  }).join('');
  box.innerHTML = html;
}

// ---- 方向拖拽排序 ----
let dragDirId = null;
function dirDragStart(e) {
  const item = e.target.closest('.dir-side-item');
  if (!item || !item.dataset.dir) return;
  dragDirId = item.dataset.dir;
  item.classList.add('dragging');
  try { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', dragDirId); } catch (err) {}
}
function dirDragOver(e) {
  const item = e.target.closest('.dir-side-item');
  if (!item || !dragDirId) return;
  e.preventDefault();
  // 只高亮当前经过的那一个，清掉之前残留的指示线
  const box = document.getElementById('bank-dir-list');
  box.querySelectorAll('.drop-target').forEach(x => x.classList.remove('drop-target'));
  if (item.dataset.dir === dragDirId) return;
  item.classList.add('drop-target');
}
function dirDrop(e) {
  e.preventDefault();
  const box = document.getElementById('bank-dir-list');
  box.querySelectorAll('.drop-target').forEach(x => x.classList.remove('drop-target'));
  if (!dragDirId) return;
  const tgt = e.target.closest('.dir-side-item[data-dir]');
  if (!tgt || tgt.dataset.dir === dragDirId) return;
  const src = box.querySelector(`.dir-side-item[data-dir="${dragDirId}"]`);
  const items = [...box.querySelectorAll('.dir-side-item[data-dir]')];
  if (items.indexOf(src) < items.indexOf(tgt)) tgt.after(src); else tgt.before(src);
  const newOrder = [...box.querySelectorAll('.dir-side-item[data-dir]')].map(x => x.dataset.dir);
  apiPost('/api/directions', { action: 'reorder', ids: newOrder }).catch(() => toast('排序保存失败'));
}
function dirDragEnd() {
  document.querySelectorAll('#bank-dir-list .dragging, #bank-dir-list .drop-target')
    .forEach(x => x.classList.remove('dragging', 'drop-target'));
  dragDirId = null;
}

// ---- 删除方向（进回收站）：就地确认，不用系统对话框 ----
let dirDelPop = null;
function closeDirDelPop() {
  if (dirDelPop) { dirDelPop.remove(); dirDelPop = null; }
}
document.addEventListener('click', (e) => {
  if (!e.target.closest('.dir-del-pop') && !e.target.closest('.dir-del')) closeDirDelPop();
});
function askDeleteDir(ev, id, name) {
  closeDirDelPop();
  const del = ev.currentTarget;
  const r = del.getBoundingClientRect();
  const pop = document.createElement('div');
  pop.className = 'dir-del-pop fixed';
  pop.innerHTML = `<span class="dir-del-pop-tx">删除「${esc(name)}」？</span>
    <button class="pop-btn ok" onclick="event.stopPropagation();closeDirDelPop();deleteDir('${id}','${esc(name)}')">确定</button>
    <button class="pop-btn" onclick="event.stopPropagation();closeDirDelPop()">取消</button>`;
  document.body.appendChild(pop);
  pop.style.left = '0px'; pop.style.top = '0px';
  const pw = pop.offsetWidth, ph = pop.offsetHeight;
  // 优先在按钮上方弹出；视口顶部不够则放下方
  let top = r.top - ph - 6;
  if (top < 8) top = r.bottom + 6;
  pop.style.top = Math.round(top) + 'px';
  pop.style.left = Math.round(Math.max(8, Math.min(window.innerWidth - pw - 8, r.right - pw))) + 'px';
  dirDelPop = pop;
}
async function deleteDir(id, name) {
  try {
    const r = await apiPost('/api/directions', { action: 'delete', direction_id: id });
    toast(r.message || '已移入回收站');
    if (state.activeDir === id) state.activeDir = null;
    state.data = await apiGet('/api/questions');
    renderDirList();
    renderBankQuestions();
    renderInterviewEntry();
    refreshTrashBadge();
  } catch (e) {
    toast('删除失败：' + e.message);
  }
}

// ---- 回收站 ----
async function openTrashModal() {
  lockBodyScroll(true);
  document.getElementById('trash-modal').classList.add('show');
  await renderTrashList();
}
function closeTrashModal() {
  lockBodyScroll(false);
  document.getElementById('trash-modal').classList.remove('show');
}
async function renderTrashList() {
  const list = document.getElementById('trash-list');
  let trash = [];
  try { trash = await apiGet('/api/directions/trash'); } catch (e) {}
  if (!trash.length) {
    list.innerHTML = '<div class="empty" style="padding:24px">回收站是空的</div>';
    return;
  }
  list.innerHTML = trash.map(d => `
    <div class="trash-item">
      <span class="adir-chip chip-${d.color || 'indigo'}">${(d.name || '').slice(0, 2) || '？'}</span>
      <span class="trash-body">
        <span class="trash-name">${esc(d.name)}</span>
        <span class="trash-meta">${d.question_count} 题${d.desc ? ' · ' + esc(d.desc) : ''}${d.deleted_at ? ' · ' + esc(d.deleted_at) : ''}</span>
      </span>
      <span class="trash-ops">
        <button class="ghost sm" onclick="restoreDir('${d.id}')">还原</button>
        <button class="ghost sm trash-del" onclick="purgeDir('${d.id}','${esc(d.name)}')">彻底删除</button>
      </span>
    </div>`).join('');
}
async function restoreDir(id) {
  try {
    const r = await apiPost('/api/directions/restore', { direction_id: id });
    toast(r.message || '已还原');
    state.data = await apiGet('/api/questions');
    renderDirList();
    renderBankQuestions();
    renderInterviewEntry();
    refreshTrashBadge();
    await renderTrashList();
  } catch (e) { toast('还原失败：' + e.message); }
}
async function purgeDir(id, name) {
  if (!confirm(`彻底删除「${name}」？该方向的题目将一并删除，不可恢复。`)) return;
  try {
    const r = await apiPost('/api/directions/purge', { direction_id: id });
    toast(r.message || '已彻底删除');
    refreshTrashBadge();
    await renderTrashList();
  } catch (e) { toast('删除失败：' + e.message); }
}
async function purgeAllTrash() {
  if (!confirm('清空回收站？所有回收站方向的题目将彻底删除，不可恢复。')) return;
  try {
    const r = await apiPost('/api/directions/purge-all', {});
    toast(r.message || '已清空');
    refreshTrashBadge();
    await renderTrashList();
  } catch (e) { toast('清空失败：' + e.message); }
}
async function refreshTrashBadge() {
  const badge = document.getElementById('trash-badge');
  try {
    const trash = await apiGet('/api/directions/trash');
    badge.style.display = trash.length ? '' : 'none';
    badge.textContent = trash.length;
  } catch (e) {}
}

function renderBankFilterChips() {
  renderBankStatusChips();
  renderBankImportanceChips();
}

// 状态组：全部 / 未掌握 / 已掌握 / 收藏 —— 组内单选，点"全部"回到默认
function renderBankStatusChips() {
  const defs = [
    ['all', '全部'], ['unmastered', '未掌握'], ['mastered', '已掌握'], ['bookmarked', '收藏'],
  ];
  const box = document.getElementById('bank-status-chips');
  box.innerHTML = defs.map(([v, label]) =>
    `<span class="filter-chip ${bankFilter.status === v ? 'on' : ''}" onclick="setBankFilter('status','${v}')">${label}</span>`
  ).join('');
}

// 重要度组：全部 / 必考 / 高频 / 中频 —— 组内单选，点"全部"回到默认
function renderBankImportanceChips() {
  const defs = [['all', '全部'], ['必考', '必考'], ['高频', '高频'], ['中频', '中频']];
  const box = document.getElementById('bank-importance-chips');
  box.innerHTML = defs.map(([v, label]) =>
    `<span class="filter-chip ${bankFilter.importance === v ? 'on' : ''}" onclick="setBankFilter('importance','${v}')">${label}</span>`
  ).join('');
}

function setActiveDir(dirId) {
  state.activeDir = dirId;
  // 记住上次看的方向（下次打开默认展示）
  try { apiPost('/api/state', { last_direction: dirId }).catch(() => {}); } catch (e) {}
  renderDirList();
  renderBankQuestions();
  // 面试设置页跟随当前方向
  const setup = document.getElementById('interview-setup');
  if (setup && setup.style.display !== 'none') renderInterviewEntry();
}

function setBankFilter(group, val) {
  if (group === 'status') bankFilter.status = val;
  else if (group === 'importance') bankFilter.importance = val;
  renderBankFilterChips();
  renderBankQuestions();
}
// 题库搜索框：实时过滤
(function () {
  const inp = document.getElementById('bank-search');
  if (inp) inp.addEventListener('input', () => {
    bankFilter.search = inp.value.trim();
    renderBankQuestions();
  });
})();

function renderBankQuestions() {
  if (!state.data) return;
  let qs = state.data.questions;
  if (state.activeDir) qs = qs.filter(q => q.direction === state.activeDir);
  if (bankFilter.importance !== 'all') qs = qs.filter(q => q.importance === bankFilter.importance);
  if (bankFilter.status === 'mastered') qs = qs.filter(q => state.userState.mastered.includes(q.id));
  else if (bankFilter.status === 'unmastered') qs = qs.filter(q => !state.userState.mastered.includes(q.id));
  else if (bankFilter.status === 'bookmarked') qs = qs.filter(q => state.userState.bookmarks.includes(q.id));
  if (bankFilter.search) {
    const k = bankFilter.search.toLowerCase();
    qs = qs.filter(q =>
      (q.question || '').toLowerCase().includes(k) ||
      (q.topic || '').toLowerCase().includes(k) ||
      (Array.isArray(q.answer) ? q.answer.join(' ') : String(q.answer || '')).toLowerCase().includes(k)
    );
  }

  const box = document.getElementById('bank-questions');
  if (!qs.length) {
    box.innerHTML = '<div class="empty">这个筛选下没有题目，换个条件试试。</div>';
    return;
  }
  box.innerHTML = qs.map(q => {
    const isMastered = state.userState.mastered.includes(q.id);
    const isBookmarked = state.userState.bookmarks.includes(q.id);
    const dirName = state.data.directions.find(d => d.id === q.direction)?.name || q.direction;
    return `
    <div class="q-item ${isMastered ? 'mastered' : ''}" onclick="toggleCard(this, event)">
      <div class="q-head">
        <div class="q-text">${isMastered ? '<span class="mastered-dot" title="已掌握"></span> ' : ''}${esc(q.question)}</div>
        <div class="q-meta">
          <span class="badge" style="color:var(--accent2);background:var(--accent2-soft);border-color:transparent">${esc(dirName)}</span>
          <span class="badge ${q.importance === '高频' || q.importance === '必考' ? 'high' : 'mid'}">${esc(q.importance)}</span>
          <span class="badge ${q.difficulty}">${esc(q.difficulty)}</span>
          ${isBookmarked ? '<span class="badge" style="color:#92400e;background:#fef3c7;border-color:transparent">★</span>' : ''}
        </div>
      </div>
      <div class="q-ans">
        <div class="label">参考答案要点</div>
        <ul>${q.answer.map(a => `<li>${esc(a)}</li>`).join('')}</ul>
        <div class="label">常见追问</div>
        <ul>${(q.followups || []).map(f => `<li class="followup" onclick="openFollowup('${q.id}','${encodeURIComponent(f)}')">${esc(f)}<span class="fu-arrow">›</span></li>`).join('')}</ul>
        <div class="q-actions" onclick="event.stopPropagation()">
          <button class="q-btn ${isMastered ? 'on' : ''}" onclick="toggleMastered('${q.id}')">${isMastered ? '✓ 已掌握' : '标记掌握'}</button>
          <button class="q-btn ${isBookmarked ? 'on bookmark-on' : ''}" onclick="toggleBookmark('${q.id}')">${isBookmarked ? '★ 已收藏' : '☆ 收藏'}</button>
        </div>
      </div>
    </div>`;
  }).join('');
}

// ================================================================ 追问对话（多实例：每次点击常见追问新开一个独立模态框，可同时叠开多个）
let fuInstances = [];   // 当前打开的追问弹窗实例（关闭即移除）
let fuZ = 50;           // 弹窗 z-index 起点，逐次递增实现层叠

// 单个追问弹窗的 HTML 模板（每次创建实例时克隆）
function fuBuildTemplate() {
  return `<div class="modal followup-modal">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px">
        <div>
          <h3>追问详解</h3>
          <div class="m-sub fu-q-title"></div>
        </div>
        <div style="display:flex;align-items:center;gap:6px;flex-shrink:0">
          <span class="fu-reset" data-act="reset" role="button" title="清空对话，重新开始">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>
          </span>
          <span class="fu-reset" data-act="min" role="button" title="最小化，暂时去干别的（回复继续生成）">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/></svg>
          </span>
          <button class="modal-close" title="关闭" aria-label="关闭">×</button>
        </div>
      </div>
      <div class="fu-chat"></div>
      <div class="fu-input">
        <textarea rows="1" placeholder="继续追问，如：能举个具体例子吗？"></textarea>
        <span class="shortcut-hint iv-hint"><span class="kbd">Enter</span> 发送 · <span class="kbd">Shift+Enter</span> 换行</span>
        <span class="followup-send" role="button" title="发送">
          <svg class="send-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
          <svg class="stop-icon" style="display:none" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>
        </span>
      </div>
    </div>`;
}

// 创建一个追问弹窗实例（独立会话 / 流式 / 关闭 / 最小化）
function createFollowupInstance(qid, autoMsg) {
  const q = (state.data?.questions || []).find(x => x.id === qid) || {};
  const inst = {
    qid, topic: autoMsg || '', msgs: [],
    abort: null, closed: false,
    overlay: null, chat: null, input: null, sendBtn: null, mini: null,
    z: ++fuZ,
  };
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay show';
  overlay.style.zIndex = inst.z;
  overlay.innerHTML = fuBuildTemplate();
  document.body.appendChild(overlay);
  inst.overlay = overlay;
  inst.chat = overlay.querySelector('.fu-chat');
  inst.input = overlay.querySelector('textarea');
  inst.sendBtn = overlay.querySelector('.followup-send');
  // 标题优先显示追问标题（同题目下多个追问可区分）；手动输入（无 topic）则回退到题目标题
  const fuTitle = inst.topic ? inst.topic : (q.question || '');
  overlay.querySelector('.fu-q-title').textContent = fuTitle.length > 60 ? fuTitle.slice(0, 60) + '…' : fuTitle;
  // 事件绑定（闭包持有 inst）
  overlay.querySelector('[data-act="reset"]').addEventListener('click', () => resetFollowup(inst));
  overlay.querySelector('[data-act="min"]').addEventListener('click', () => minimizeFollowup(inst));
  overlay.querySelector('.modal-close').addEventListener('click', () => closeFollowupModal(inst));
  inst.sendBtn.addEventListener('click', () => sendFollowup(inst));
  inst.input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendFollowup(inst); }
  });
  inst.input.addEventListener('input', () => {
    inst.input.style.height = 'auto';
    inst.input.style.height = Math.min(inst.input.scrollHeight, 160) + 'px';
  });
  fuInstances.push(inst);
  lockBodyScroll(true);
  // 加载该追问的会话历史；无历史且是常见追问 → 自动发起
  (async () => {
    inst.chat.innerHTML = '<div class="fu-loading">加载对话…</div>';
    try {
      const r = await apiGet('/api/followup/' + qid + '?topic=' + encodeURIComponent(inst.topic));
      inst.msgs = r.messages || [];
      renderFollowupMessages(inst);
      const lastUserMsg = [...inst.msgs].reverse().find(m => m.role === 'user');
      if (inst.topic && (!lastUserMsg || lastUserMsg.content !== inst.topic)) {
        sendFollowup(inst, inst.topic);
      }
    } catch (e) {
      inst.chat.innerHTML = '<div class="fu-error">加载失败：' + esc(e.message) + '</div>';
    }
  })();
  return inst;
}

// 常见追问入口：每次都新开一个独立模态框
function openFollowup(qid, encodedQ) {
  const autoMsg = encodedQ ? decodeURIComponent(encodedQ) : '';
  createFollowupInstance(qid, autoMsg);
}

// 渲染某实例的消息列表
function renderFollowupMessages(inst) {
  if (!inst.chat) return;
  inst.chat.innerHTML = inst.msgs.map(m => {
    const cls = m.role === 'user' ? 'user' : 'assistant';
    const content = m.role === 'assistant' ? renderMarkdown(m.content) : esc(m.content);
    return `<div class="fu-msg ${cls}">${content}</div>`;
  }).join('') || '<div class="fu-empty">点击一个常见追问开始，或直接在下方输入你的问题。</div>';
  inst.chat.scrollTop = inst.chat.scrollHeight;
}

// 切换某实例发送按钮图标：send=纸飞机，stop=停止
function setFollowupSendBtn(inst, mode) {
  const btn = inst.sendBtn;
  if (!btn) return;
  const sendI = btn.querySelector('.send-icon');
  const stopI = btn.querySelector('.stop-icon');
  if (mode === 'stop') {
    btn.classList.add('stopping');
    btn.title = '停止生成';
    if (sendI) sendI.style.display = 'none';
    if (stopI) stopI.style.display = '';
  } else {
    btn.classList.remove('stopping');
    btn.title = '发送';
    if (sendI) sendI.style.display = '';
    if (stopI) stopI.style.display = 'none';
  }
}

async function sendFollowup(inst, preMsg) {
  if (!inst || inst.closed) return;
  // 生成中再次点击 -> 停止生成
  if (inst.abort) { inst.abort.abort(); return; }
  const msg = (preMsg !== undefined ? preMsg : inst.input.value).trim();
  if (!msg) return;
  if (preMsg === undefined) inst.input.value = '';
  inst.msgs.push({ role: 'user', content: msg });
  renderFollowupMessages(inst);
  setFollowupSendBtn(inst, 'stop');
  const chat = inst.chat;
  // 流式气泡：思考折叠区 + Markdown 正文区 + 三点加载指示
  const bubble = document.createElement('div');
  bubble.className = 'fu-msg assistant';
  bubble.innerHTML = '<details class="think" style="display:none"><summary>思考中</summary><div class="think-body"></div></details><div class="say"></div><div class="fu-dots"><i></i><i></i><i></i></div>';
  chat.appendChild(bubble);
  chat.scrollTop = chat.scrollHeight;
  const think = bubble.querySelector('.think');
  const thinkBody = bubble.querySelector('.think-body');
  const say = bubble.querySelector('.say');
  const dots = bubble.querySelector('.fu-dots');
  let full = '', errMsg = null, aborted = false, renderPending = false, firstContent = true;
  const scheduleRender = () => {
    if (renderPending) return;
    renderPending = true;
    requestAnimationFrame(() => {
      renderPending = false;
      say.innerHTML = renderMarkdown(full);
      chat.scrollTop = chat.scrollHeight;
    });
  };
  const ctrl = new AbortController();
  inst.abort = ctrl;
  try {
    await apiStream('/api/followup/ask/stream', { question_id: inst.qid, message: msg, topic: inst.topic }, (ev, p) => {
      if (ev === 'reasoning') {
        think.style.display = ''; thinkBody.textContent += p.t || '';
        chat.scrollTop = chat.scrollHeight;
      } else if (ev === 'content') {
        if (firstContent) { firstContent = false; dots.style.display = 'none'; }
        full += p.t || ''; scheduleRender();
      } else if (ev === 'done') {
        if (p.messages) inst.msgs = p.messages;
      } else if (ev === 'error') {
        errMsg = p.message || '未知错误';
      }
    }, ctrl.signal);
  } catch (e) {
    if (e && e.name === 'AbortError') aborted = true;
    else errMsg = e.message;
  } finally {
    if (inst.abort === ctrl) inst.abort = null;
    setFollowupSendBtn(inst, 'send');
  }
  if (inst.closed) return;  // 弹窗已关闭，不再更新
  dots.style.display = 'none';
  if (aborted) {
    // 用户主动停止：后端保留用户消息、未落盘半截回答，这里刷新真实历史
    bubble.remove();
    try { const s = await apiGet('/api/followup/' + inst.qid + '?topic=' + encodeURIComponent(inst.topic)); inst.msgs = s.messages || []; } catch (e) {}
    renderFollowupMessages(inst);
  } else if (errMsg) {
    // 后端已回滚用户消息，本地过滤脏消息并提示
    inst.msgs = inst.msgs.filter(m => m.content !== msg || m.role !== 'user');
    renderFollowupMessages(inst);
    chat.insertAdjacentHTML('beforeend', '<div class="fu-error">发送失败：' + esc(errMsg) + '</div>');
  } else {
    if (!inst.msgs.length) {
      try { const s = await apiGet('/api/followup/' + inst.qid + '?topic=' + encodeURIComponent(inst.topic)); inst.msgs = s.messages || []; } catch (e) {}
    }
    renderFollowupMessages(inst);
  }
  if (!inst.closed) inst.input.focus();
}

function closeFollowupModal(inst) {
  if (!inst || inst.closed) return;
  inst.closed = true;
  if (inst.abort) inst.abort.abort();
  if (inst.mini && inst.mini.parentNode) inst.mini.remove();
  if (inst.overlay && inst.overlay.parentNode) inst.overlay.remove();
  lockBodyScroll(false);
  fuInstances = fuInstances.filter(x => x !== inst);
}

// 最小化某实例：收成底部悬浮条，可暂时去干别的（正在生成的回复不中断）
function minimizeFollowup(inst) {
  if (!inst || inst.closed) return;
  inst.overlay.classList.remove('show');
  lockBodyScroll(false);
  const q = (state.data?.questions || []).find(x => x.id === inst.qid) || {};
  // 悬浮条标题同样优先显示追问标题（同题目多个追问可区分）
  const t = inst.topic ? inst.topic : (q.question || '追问');
  if (!inst.mini) {
    const mini = document.createElement('div');
    mini.className = 'mini-bar';
    mini.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg><span class="mini-tag">追问</span><span class="mini-tx"></span><button class="mini-close" title="关闭">×</button>';
    mini.addEventListener('click', (e) => {
      if (e.target.closest('.mini-close')) closeFollowupModal(inst);
      else restoreFollowup(inst);
    });
    inst.mini = mini;
  }
  inst.mini.querySelector('.mini-tx').textContent = t.length > 30 ? t.slice(0, 30) + '…' : t;
  inst.mini.classList.add('show');
  document.getElementById('minis-stack').appendChild(inst.mini); // 追加到末尾 → 堆叠在最上
}
function restoreFollowup(inst) {
  if (!inst || inst.closed) return;
  if (inst.mini) inst.mini.classList.remove('show');
  inst.overlay.classList.add('show');
  lockBodyScroll(true);
  inst.chat.scrollTop = inst.chat.scrollHeight;
  inst.input.focus();
}

// 轻量 Markdown 渲染（先转义 HTML 再解析，安全；覆盖 AI 回复常见语法）
function inlineMd(s) {
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  return s;
}
function renderMarkdown(text) {
  if (!text) return '';
  const escaped = esc(text);
  const codeBlocks = [];
  let body = escaped.replace(/```[\s\S]*?```/g, m => {
    const id = '$$CODE' + codeBlocks.length + '$$';
    codeBlocks.push(m.slice(3, -3).trim());
    return id;
  });
  let out = '', inList = null, inPara = false;
  const closePara = () => { if (inPara) { out += '</p>'; inPara = false; } };
  const closeList = () => { if (inList) { out += '</' + inList + '>'; inList = null; } };
  for (const raw of body.split('\n')) {
    const line = raw;
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) { closePara(); closeList(); out += `<h${h[1].length}>${inlineMd(h[2])}</h${h[1].length}>`; continue; }
    if (/^\$\$CODE\d+\$\$$/.test(line.trim())) { closePara(); closeList(); out += line; continue; }
    if (!line.trim()) { closePara(); closeList(); continue; }
    const ul = line.match(/^\s*[-*]\s+(.*)$/);
    if (ul) { closePara(); if (inList !== 'ul') { closeList(); inList = 'ul'; out += '<ul>'; } out += `<li>${inlineMd(ul[1])}</li>`; continue; }
    const ol = line.match(/^\s*\d+\.\s+(.*)$/);
    if (ol) { closePara(); if (inList !== 'ol') { closeList(); inList = 'ol'; out += '<ol>'; } out += `<li>${inlineMd(ol[1])}</li>`; continue; }
    closeList();
    if (!inPara) { out += '<p>'; inPara = true; } else out += '<br>';
    out += inlineMd(line);
  }
  closePara(); closeList();
  return out.replace(/\$\$CODE(\d+)\$\$/g, (m, i) => `<pre><code>${codeBlocks[i]}</code></pre>`);
}

// 整卡可点：点题卡任意非答案区位置开/关，点 q-ans 内部（答案、追问、按钮）不收起
function toggleCard(card, ev) {
  if (ev.target.closest('.q-ans')) return;
  card.classList.toggle('open');
}

// 重置某实例的追问：清空该追问记录，回到首次询问（重新发起这个常见追问）
function resetFollowup(inst) {
  if (!inst || inst.closed) return;
  showConfirm('将清空这个追问的记录，重新开始。确定重置？', async () => {
    try { await apiPost('/api/followup/reset', { question_id: inst.qid, topic: inst.topic }); } catch (e) { /* 后端即使失败也继续清空本地界面 */ }
    inst.msgs = [];
    renderFollowupMessages(inst);
    if (inst.topic) {
      sendFollowup(inst, inst.topic);
    } else {
      inst.input.focus();
    }
  }, '重置');
}


// ---------- 通用确认框（替代浏览器原生 confirm） ----------
let confirmCb = null;
function showConfirm(text, onOk, okText) {
  document.getElementById('confirm-text').textContent = text;
  document.getElementById('confirm-ok-btn').textContent = okText || '确定';
  confirmCb = onOk;
  document.getElementById('confirm-modal').classList.add('show');
  lockBodyScroll(true);
}
function closeConfirmModal() {
  document.getElementById('confirm-modal').classList.remove('show');
  lockBodyScroll(false);
  confirmCb = null;
}
function doConfirm() {
  const cb = confirmCb;
  closeConfirmModal();
  if (cb) cb();
}
// ================================================================ 面试：按方向入口（跟随左侧选择）
function renderInterviewEntry() {
  if (!state.data) return;
  const box = document.getElementById('dir-interview-entry');
  if (!box) return;
  const dirs = state.data.directions || [];
  const exclude = state.userState.mix_exclude || [];
  const cards = dirs.map(d => {
    const count = state.data.questions.filter(q => q.direction === d.id).length;
    const mastered = state.data.questions.filter(q => q.direction === d.id && state.userState.mastered.includes(q.id)).length;
    const off = exclude.includes(d.id);
    return `<div class="dir-card ${off ? 'mix-off' : ''}">
      <div class="dir-card-head">
        <div class="dir-chip ${DIR_CHIP[d.id] || 'chip-' + (d.color || 'indigo')}">${DIR_SHORT[d.id] || (d.name || '').slice(0, 2) || '？'}</div>
        <div class="dir-name">${esc(d.name)}</div>
        <span class="dir-mix-toggle ${off ? 'off' : ''}" onclick="event.stopPropagation();toggleMixDir('${d.id}')" title="${off ? '当前已从综合混考排除，点击启用' : '当前参与综合混考，点击禁用'}">
          ${off
            ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="1" y="5" width="22" height="14" rx="7"/><circle cx="8" cy="12" r="3" fill="currentColor"/></svg>'
            : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="1" y="5" width="22" height="14" rx="7"/><circle cx="16" cy="12" r="3" fill="currentColor"/></svg>'}
        </span>
      </div>
      <div class="dir-desc">${esc(d.desc)}</div>
      <div class="dir-card-foot">
        <div class="dir-stat">${count} 题 · 已掌握 ${mastered}</div>
        <button class="dir-go" onclick="startInterview('${d.id}')">开始面试</button>
      </div>
    </div>`;
  }).join('');
  box.innerHTML = cards || '<div class="empty" style="grid-column:1/-1;padding:24px">暂无方向，请先到「题库刷题」新建方向</div>';
}

async function toggleMixDir(did) {
  const exclude = new Set(state.userState.mix_exclude || []);
  if (exclude.has(did)) exclude.delete(did); else exclude.add(did);
  state.userState.mix_exclude = [...exclude];
  renderInterviewEntry();
  try { await apiPost('/api/state', { mix_exclude: state.userState.mix_exclude }); } catch (e) {}
}

// ================================================================ 题库刷题
async function initBank() {
  const data = await apiGet('/api/questions');
  state.data = data;
  await loadUserState();
  // 默认显示上次的方向（若有），否则全部
  state.activeDir = state.userState.last_direction || null;
  bankFilter = { importance: 'all', status: 'all' };
  renderDirList();
  renderBankFilterChips();
  renderBankQuestions();
  renderInterviewEntry();
  refreshTrashBadge();
}

let bankFilter = { importance: 'all', status: 'all', search: '' };

// ================================================================ 模拟面试（聊天式）
let intQNo = 0;   // 当前题号
let intTotal = 10;  // 本场目标题数（后端下发，AI 插入题不计入）
let intCurAnswered = false; // 当前题是否已提交过回答（现场出题据此判断替换 or 下一题）

// ---------------------------------------------------------------- 断点续面
let liveSession = null;
const RESEND_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><line x1="12" y1="7.5" x2="12" y2="13"/><circle cx="12" cy="16.6" r="0.4" fill="currentColor"/></svg>';

// 重发当前题最后一条"未收到回复"的回答：回滚后原样重新发送
async function resendLastAnswer(btn) {
  if (!state.sessionId) return;
  const bubble = btn.closest('.msg');
  const text = (bubble && bubble.querySelector('.txt')) ? bubble.querySelector('.txt').textContent : '';
  if (!text.trim()) { toast('没有可重发的内容'); return; }
  try {
    const r = await apiPost('/api/session/retry', { session_id: state.sessionId });
    if (bubble && bubble.parentNode) bubble.remove();
    $('#int-answer').value = text;
    await submitAnswer();
  } catch (e) {
    toast('重发失败：' + e.message);
  }
}

// 检查是否有未完成的面试会话，有则显示恢复条 + 顶部 tab 角标
async function checkLiveInterview() {
  const wrap = document.getElementById('live-resume');
  const badge = document.getElementById('tab-live-badge');
  try {
    const r = await apiGet('/api/session/live');
    const arr = (r && r.live) || [];
    liveSession = arr[0] || null;
    if (liveSession) {
      if (badge) { badge.style.display = ''; badge.textContent = '未完成'; }
      if (wrap) {
        document.getElementById('lr-dir').textContent = liveSession.direction_name || '';
        document.getElementById('lr-answered').textContent = liveSession.answered || 0;
        document.getElementById('lr-total').textContent = liveSession.total || 10;
        wrap.style.display = '';
      }
    } else {
      if (badge) badge.style.display = 'none';
      if (wrap) wrap.style.display = 'none';
    }
  } catch (e) {
    if (badge) badge.style.display = 'none';
    if (wrap) wrap.style.display = 'none';
  }
}

// 从恢复条继续一场未完成的面试：重建对话流到中断点
function resumeInterview() {
  if (!liveSession) return;
  const s = liveSession;
  state.sessionId = s.session_id;
  state.direction = s.direction;
  document.getElementById('interview-setup').style.display = 'none';
  document.getElementById('interview-room').style.display = 'flex';
  document.getElementById('interview-result').style.display = 'none';
  document.getElementById('int-qinfo').style.display = 'none';
  setIntSendBtn('send');
  $('#int-answer').value = '';
  $('#int-status').textContent = '';
  document.getElementById('int-back-btn').style.display = 'none'; // 已作答，不允许直接返回
  document.getElementById('int-end-btn').style.display = '';
  document.getElementById('int-ai-btn').style.display = '';
  document.getElementById('int-skip-btn').style.display = '';
  $('#int-dir-name').textContent = s.direction_name || '';
  intTotal = s.total || 10;
  $('#int-total').textContent = intTotal;
  const chatBox = document.getElementById('int-chat');
  chatBox.innerHTML = '';
  intQNo = 0;
  // 已答历史：每道题渲染 题卡片 + 完整对话块
  (s.answers || []).forEach(a => {
    intQNo += 1;
    const impCls = { '必考': 'imp-must', '高频': 'imp-hot', '中频': 'imp-mid', '低频': 'imp-low' }[a.importance] || '';
    const diffCls = { 'easy': 'd-easy', 'medium': 'd-medium', 'hard': 'd-hard' }[a.difficulty] || '';
    const div = document.createElement('div');
    div.className = 'msg ai q-msg';
    div.innerHTML = `
      <div class="iv-qcard">
        <div class="iv-qcard-tags">
          <span class="q-topic">${esc(a.topic || '综合题')}</span>
          ${a.importance ? `<span class="q-meta-badge ${impCls}">${esc(a.importance)}</span>` : ''}
          ${a.difficulty ? `<span class="q-meta-badge ${diffCls}">${esc(a.difficulty)}</span>` : ''}
          <span class="q-no">第 ${intQNo} 题</span>
        </div>
        <div class="iv-qcard-text">${esc(a.question || '')}</div>
      </div>`;
    chatBox.appendChild(div);
    const d = document.createElement('div');
    d.className = 'msg me';
    d.innerHTML = `<div class="bubble pre">${esc(a.answer || '')}</div>`;
    chatBox.appendChild(d);
  });
  // 当前题
  intQNo += 1;
  const cq = s.current_q;
  state.currentQid = cq ? cq.id : s.current;
  if (cq && cq.question) {
    showQuestion({ topic: cq.topic, importance: cq.importance, difficulty: cq.difficulty, question: cq.question }, intQNo);
  }
  // 当前题的对话
  const dl = s.dialogue || [];
  intCurAnswered = dl.some(d => d.role === 'candidate');  // 断点恢复：当前题是否已有回答
  dl.forEach((d, idx) => {
    const isLast = idx === dl.length - 1;
    if (d.role === 'candidate') {
      // 最后一条是候选人的回答且面试官还没回复 -> 显示"重新发送"
      if (isLast) {
        const div = document.createElement('div');
        div.className = 'msg me resendable';
        div.innerHTML = `<button class="resend-btn" onclick="resendLastAnswer(this)" title="重新发送">${RESEND_ICON}</button><div class="bubble"><span class="txt">${esc(d.text || '')}</span></div>`;
        chatBox.appendChild(div);
      } else {
        addChat('me', esc(d.text || ''));
      }
    } else addChat('ai', `<div class="bubble">${esc(d.text || '')}</div>`);
  });
  chatBox.scrollTop = chatBox.scrollHeight;
  $('#int-count').textContent = intQNo;
  $('#int-progress').style.width = Math.min(100, (intQNo / intTotal) * 100) + '%';
  $('#int-answer').focus();
}

function startInterview(dirId) {
  state.direction = dirId;
  intQNo = 0;
  document.getElementById('interview-setup').style.display = 'none';
  document.getElementById('interview-room').style.display = 'flex';
  document.getElementById('interview-result').style.display = 'none';
  document.getElementById('int-qinfo').style.display = 'none';
  document.getElementById('int-chat').innerHTML = '';
  setIntSendBtn('send');
  $('#int-answer').value = '';
  $('#int-status').textContent = '正在出题…';
  // 未作答时允许返回；首次作答后隐藏
  document.getElementById('int-back-btn').style.display = '';
  // 评分后恢复顶部操作按钮
  document.getElementById('int-end-btn').style.display = '';
  document.getElementById('int-ai-btn').style.display = '';
  document.getElementById('int-skip-btn').style.display = '';
  // 保存上次选择的方向
  try { apiPost('/api/state', { last_direction: dirId }).catch(() => {}); } catch (e) {}
  const body = { direction: dirId };
  if (dirId === 'all') body.exclude_dirs = state.userState.mix_exclude || [];
  apiPost('/api/session/new', body)
    .then(d => {
      state.sessionId = d.session_id;
      $('#int-dir-name').textContent = d.direction;
      intTotal = d.total || 10;
      $('#int-total').textContent = intTotal;
      $('#int-count').textContent = d.answered + 1;
      $('#int-progress').style.width = Math.min(100, ((d.answered + 1) / intTotal) * 100) + '%';
      showQuestion(d.question, 1);
      state.currentQid = d.question.id;
      $('#int-status').textContent = '';
      $('#int-answer').focus();
    })
    .catch(e => { $('#int-status').textContent = '出题失败：' + e.message; });
}

// 未作答时返回：丢弃当前会话（不生成报告），回到面试设置页
function backToSetup() {
  if (state.sessionId) {
    apiPost('/api/session/end', { session_id: state.sessionId }).catch(() => {});
  }
  state.sessionId = null;
  document.getElementById('interview-room').style.display = 'none';
  document.getElementById('interview-setup').style.display = '';
  document.getElementById('interview-result').style.display = 'none';
  checkLiveInterview();
}

function showQuestion(q, no) {
  intQNo = no;
  intCurAnswered = false;  // 新题出现，默认未回答
  // 题目以卡片形式展示在对话流里，不再用顶部信息条
  document.getElementById('int-qinfo').style.display = 'none';
  const impCls = { '必考': 'imp-must', '高频': 'imp-hot', '中频': 'imp-mid', '低频': 'imp-low' }[q.importance] || '';
  const diffCls = { 'easy': 'd-easy', 'medium': 'd-medium', 'hard': 'd-hard' }[q.difficulty] || '';
  const chatBox = document.getElementById('int-chat');
  const div = document.createElement('div');
  div.className = 'msg ai q-msg';
  div.innerHTML = `
    <div class="iv-qcard">
      <div class="iv-qcard-tags">
        <span class="q-topic">${esc(q.topic || '综合题')}</span>
        ${q.importance ? `<span class="q-meta-badge ${impCls}">${esc(q.importance)}</span>` : ''}
        ${q.difficulty ? `<span class="q-meta-badge ${diffCls}">${esc(q.difficulty)}</span>` : ''}
        <span class="q-no">第 ${no} 题</span>
      </div>
      <div class="iv-qcard-text">${esc(q.question)}</div>
    </div>`;
  chatBox.appendChild(div);
  chatBox.scrollTop = chatBox.scrollHeight;
}

async function skipQuestion() {
  if (!state.sessionId) return;
  try {
    const d = await apiPost('/api/session/skip', { session_id: state.sessionId });
    addChat('me', '（跳过此题）');
    if (d.finished) {
      $('#int-status').textContent = '全部答完！';
      await endInterview(true);
      return;
    }
    if (d.next_question) {
      applyNext(d.answered, d.next_question);
    }
  } catch (e) {
    toast('跳过失败：' + e.message);
  }
}

function addChat(role, html) {
  const chatBox = document.getElementById('int-chat');
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  div.innerHTML = html;
  chatBox.appendChild(div);
  chatBox.scrollTop = chatBox.scrollHeight;
}

function renderResult(r) { /* 不再使用（评分统一在结束后展示） */ }

let intAbort = null;  // 模拟面试流式的 AbortController

// 面试回答输入框：默认内容多行高度，输入时自动增高（上限 160px）
(function () {
  const ta = document.getElementById('int-answer');
  if (!ta) return;
  ta.addEventListener('input', () => {
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 160) + 'px';
  });
})();

// 切换面试发送按钮图标：send=纸飞机，stop=停止（与追问一致）；disabled=禁用（评分等阶段）
function setIntSendBtn(mode) {
  const btn = document.getElementById('int-submit');
  if (mode === 'disabled') {
    btn.classList.add('disabled');
    btn.classList.remove('stopping');
    return;
  }
  btn.classList.remove('disabled');
  const sendI = btn.querySelector('.send-icon');
  const stopI = btn.querySelector('.stop-icon');
  if (mode === 'stop') {
    btn.classList.add('stopping');
    btn.title = '停止生成';
    if (sendI) sendI.style.display = 'none';
    if (stopI) stopI.style.display = '';
  } else {
    btn.classList.remove('stopping');
    btn.title = '发送';
    if (sendI) sendI.style.display = '';
    if (stopI) stopI.style.display = 'none';
  }
}

async function submitAnswer() {
  // 生成中再次点击 -> 停止生成
  if (intAbort) { intAbort.abort(); return; }
  const answer = $('#int-answer').value.trim();
  if (!answer) { toast('先写点回答再发送'); return; }
  setIntSendBtn('stop');
  $('#int-status').textContent = '';
  // 已作答 -> 隐藏返回按钮（此后只能走"提前结束"）
  document.getElementById('int-back-btn').style.display = 'none';
  // 清空并重置输入框高度（回到单行），再发送消息并滚到底部
  $('#int-answer').value = '';
  $('#int-answer').style.height = 'auto';
  $('#int-answer').style.height = '';
  addChat('me', esc(answer));
  intCurAnswered = true;  // 当前题已有回答
  const intChatBox = document.getElementById('int-chat');
  if (intChatBox) intChatBox.scrollTop = intChatBox.scrollHeight;
  const bubble = createStreamBubble('int-chat', 'ai', '面试官思考中');
  let meta = null, errMsg = null, aborted = false;
  const ctrl = new AbortController();
  intAbort = ctrl;
  try {
    await apiStream('/api/session/answer/stream',
      { session_id: state.sessionId, question_id: state.currentQid, answer },
      (ev, p) => {
        if (ev === 'reasoning') bubble.reasoning(p.t || '');
        else if (ev === 'content') bubble.content(p.t || '');
        else if (ev === 'meta') meta = p;
        else if (ev === 'error') errMsg = p.message || '未知错误';
      }, ctrl.signal);
  } catch (e) {
    if (e && e.name === 'AbortError') aborted = true;
    else errMsg = e.message;
  } finally {
    intAbort = null;
    setIntSendBtn('send');
  }
  if (aborted) {
    bubble.setText('（已停止生成）');
    $('#int-answer').focus();
    return;
  }
  bubble.finish('面试官的思考');
  if (errMsg || !meta) {
    bubble.setText('发送失败：' + (errMsg || '没有收到回复，请重试'));
    $('#int-status').textContent = '';
    toast('发送失败，请重试');
    $('#int-answer').focus();
    return;
  }
  // 面试官说完 -> 收尾则下一题，否则继续追问本题
  if (meta.action === 'done') {
    if (meta.finished) {
      $('#int-status').textContent = '全部答完！';
      await endInterview(true);
      return;
    } else if (meta.next_question) {
      applyNext(meta.answered, meta.next_question);
      return;
    }
  }
  $('#int-answer').focus();
}

function applyNext(answered, nq) {
  intQNo += 1;  // 答题或跳过都推进题号，避免跳过导致题号不更新
  $('#int-count').textContent = intQNo;
  $('#int-progress').style.width = Math.min(100, (intQNo / intTotal) * 100) + '%';
  state.currentQid = nq.id;
  showQuestion(nq, intQNo);
  setIntSendBtn('send');
  $('#int-answer').focus();
}

async function aiNewQuestion() {
  if (!state.sessionId) return;
  const btn = document.getElementById('int-ai-btn');
  btn.disabled = true;
  btn.textContent = '出题中…';
  try {
    const d = await apiPost('/api/session/generate', { session_id: state.sessionId });
    state.currentQid = d.question.id;
    // 当前题还没回答 -> 现场题替换当前题（题号不变）；已回答过 -> 作为下一题
    if (intCurAnswered) {
      intQNo += 1;
    } else {
      // 移除当前未回答的题卡片，由新题顶替
      const chatBox = document.getElementById('int-chat');
      const cards = chatBox.querySelectorAll('.msg.ai.q-msg');
      const last = cards[cards.length - 1];
      if (last) last.remove();
    }
    $('#int-count').textContent = intQNo;
    $('#int-progress').style.width = Math.min(100, (intQNo / intTotal) * 100) + '%';
    $('#int-answer').value = '';
    showQuestion(d.question, intQNo);
    setIntSendBtn('send');
    $('#int-answer').focus();
  } catch (e) {
    toast('现场出题失败：' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '现场出题';
  }
}

async function endInterview(alreadyFinished = false) {
  if (!state.sessionId) return;
  $('#int-end-btn').style.display = 'none';
  $('#int-ai-btn').style.display = 'none';
  $('#int-skip-btn').style.display = 'none';
  $('#int-back-btn').style.display = 'none';
  setIntSendBtn('disabled');
  $('#int-status').innerHTML = '<span class="loading"></span>AI 正在批量评分全部题目…（约 1-2 分钟，429 自动重试）';
  try {
    const d = await apiPost('/api/session/end', { session_id: state.sessionId });
    if (d.empty) {
      // 一道题都没作答：不生成空报告，直接返回面试首页
      toast('一道题都没答，已回到面试首页');
      resetInterview();
      return;
    }
    addChat('ai', '好，今天的面试到此为止。我现在一次性批改你的全部回答，稍等片刻。');
    showReport(d.report, d.results || []);
  } catch (e) {
    $('#int-status').textContent = '批量评分失败：' + e.message;
    setIntSendBtn('send');
    $('#int-skip-btn').style.display = '';
    $('#int-back-btn').style.display = '';
    toast('评分失败，请重试');
  }
}

function decisionCardHTML(dec, decRing) {
  if (!dec || !dec.decision) return decRing || '';
  const cls = ['hire','borderline','no_hire'].includes(dec.decision) ? dec.decision : 'unknown';
  const label = esc(dec.decision_label || dec.decision);
  return `
    <div class="dec-card ${cls}">
      ${decRing || ''}
      <div class="dec-head">
        <div class="dec-badge">${label}</div>
        <div class="dec-meta">
          ${dec.level ? `<span class="dec-tag">${esc(dec.level)}</span>` : ''}
          ${dec.confidence ? `<span class="dec-tag">把握：${esc(dec.confidence)}</span>` : ''}
        </div>
      </div>
      ${dec.reason ? `<div class="dec-reason">${esc(dec.reason)}</div>` : ''}
      <div class="dec-cols">
        <div class="dec-box"><h4 class="dec-col-title good"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>亮点</h4>${(dec.strong_points && dec.strong_points.length) ? `<ul>${dec.strong_points.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : `<div class="dec-none">无</div>`}</div>
        <div class="dec-box"><h4 class="dec-col-title bad"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>硬伤</h4>${(dec.blocking_issues && dec.blocking_issues.length) ? `<ul>${dec.blocking_issues.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : `<div class="dec-none">无</div>`}</div>
      </div>
      ${(dec.advice && dec.advice.length) ? `<div class="dec-box dec-advice" style="margin-top:14px"><h4 style="color:var(--accent)">下一步怎么补</h4><ol>${dec.advice.map((x, i) => `<li><span class="adv-no">${i + 1}</span><span class="adv-tx">${esc(x)}</span></li>`).join('')}</ol></div>` : ''}
    </div>`;
}

function questionDetailHTML(r, i, sessIdx) {
  const rc = r.score >= 90 ? 'var(--good)' : r.score >= 80 ? 'var(--warn)' : 'var(--bad)';
  return `<div style="border:1px solid var(--line);border-left:3px solid ${rc};border-radius:10px;padding:14px 16px;margin-bottom:10px;background:var(--sheet)">
    <div style="display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:6px">
      <div style="font-size:14px;font-weight:700">${i+1}. ${esc(r.question || '')}</div>
      <span style="font-size:22px;font-weight:800;color:${rc};flex-shrink:0">${r.score}</span>
    </div>
    <div style="font-size:12px;color:var(--dim);margin-bottom:8px">${esc(r.topic || '')}</div>
    <details style="margin-bottom:8px">
      <summary style="cursor:pointer;font-size:12.5px;color:var(--accent2);font-weight:700">我的回答（点开看）</summary>
      <div style="margin-top:8px;font-size:13px;line-height:1.75;color:var(--ink-soft);white-space:pre-wrap;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px 12px">${esc(r.answer || '')}</div>
    </details>
    <div style="font-size:13px;line-height:1.75;color:var(--ink-soft)">${esc(r.comment || '')}</div>
    <div class="rv-detail-grid">
      <div class="rv-detail-box"><h5 style="color:var(--good)">优点</h5><ul>${(r.strengths && r.strengths.length) ? r.strengths.map(x=>`<li>${esc(x)}</li>`).join('') : '<li>无</li>'}</ul></div>
      ${(r.weaknesses && r.weaknesses.length) ? `<div class="rv-detail-box"><h5 style="color:var(--bad)">弱点</h5><ul>${r.weaknesses.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div>` : ''}
      ${(r.key_points && r.key_points.length) ? `<div class="rv-detail-box"><h5 style="color:var(--accent)">核心采分点</h5><ul>${r.key_points.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div>` : ''}
      ${(r.missed_points && r.missed_points.length) ? `<div class="rv-detail-box"><h5 style="color:var(--warn)">遗漏的点</h5><ul>${r.missed_points.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div>` : ''}
    </div>
    <div class="q-detail-btns">
      ${r.question_id ? (() => {
        const on = state.userState.mastered.includes(r.question_id);
        return `<button class="q-btn ${on ? 'on' : ''}" onclick="toggleMasteredQuick('${r.question_id}', this)"><svg class="rv-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg><span class="rv-btn-label">${on ? '已掌握' : '标记掌握'}</span></button>`;
      })() : ''}
      <button class="ghost" onclick="openReview(${i}, ${sessIdx === undefined ? -1 : sessIdx})"><svg class="rv-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>追问这题</button>
    </div>
  </div>`;
}

// 完整报告的通用内容区（决策卡片 + 得分总览 + 薄弱知识点 + 高频弱点 + 逐题复盘）
// sessIdx: 传历史场次索引时，"追问/反驳"按钮可定位到该场次的明细；-1 表示走本场结果(lastResults)
function fullReportHTML(rep, results = [], sessIdx = -1) {
  const verdictColor = rep.total_score >= 80 ? 'var(--good)' : rep.total_score >= 60 ? 'var(--warn)' : 'var(--bad)';
  // 得分环：浮动在录用判定卡片右上角，正文文字环绕
  const decRing = `
    <div class="dec-ring">
      <div class="score-ring" style="--pct:${Math.max(rep.total_score, 2)}">
        <div class="num">${rep.total_score}</div>
        <div class="lbl">场均得分</div>
      </div>
      <div class="dec-ring-line" style="color:${verdictColor}">${esc(rep.verdict || '')}</div>
      <div class="dec-ring-meta">最高 ${rep.best} · 最低 ${rep.worst}</div>
    </div>`;
  let html = decisionCardHTML(rep.decision, decRing);
  if (rep.weakness_topics && rep.weakness_topics.length) {
    html += `<div class="panel">
      <div style="color:var(--bad);font-weight:800;font-size:13px;letter-spacing:.06em;margin-bottom:14px">薄弱知识点（低于 80 分）</div>
      ${rep.weakness_topics.map(w => `
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">
          <span style="width:150px;font-size:13px;font-weight:600">${esc(w.topic)}</span>
          <div class="progress-bar" style="flex:1;margin:0"><div class="fill" style="width:${w.avg_score}%;background:var(--bad)"></div></div>
          <span style="font-size:12px;width:36px;text-align:right;font-weight:700">${w.avg_score}</span>
        </div>`).join('')}
    </div>`;
  }
  if (rep.top_weaknesses && rep.top_weaknesses.length) {
    html += `<div class="panel">
      <div style="color:var(--warn);font-weight:800;font-size:13px;letter-spacing:.06em;margin-bottom:12px">高频弱点</div>
      <div class="weak-list">${rep.top_weaknesses.map(w => `
        <div class="weak-item">
          <span class="weak-dot"></span>
          <span class="weak-text">${esc(w.weakness)}</span>
          <span class="weak-count">×${w.count}</span>
        </div>`).join('')}
      </div>
    </div>`;
  }
  if (results && results.length) {
    html += `<div class="panel">
      <div style="color:var(--ink-soft);font-weight:800;font-size:13px;letter-spacing:.06em;margin-bottom:14px">逐题复盘</div>
      ${results.map((r, i) => questionDetailHTML(r, i, sessIdx)).join('')}
    </div>`;
  }
  return html;
}

function showReport(rep, results = []) {
  const box = document.getElementById('interview-result');
  state.lastResults = results;
  let html = fullReportHTML(rep, results);
  html += `<div style="text-align:center;margin-top:20px">
    <button onclick="resetInterview()">再打一场</button>
    <button class="ghost" onclick="showTab('report')" style="margin-left:10px">查看历史报告</button>
  </div>`;
  box.innerHTML = html;
  box.style.display = 'block';
  document.getElementById('interview-room').style.display = 'none';
  document.getElementById('interview-setup').style.display = 'none';
}

// 弱点报告页：查看某场完整报告（切到独立页面展示，避免嵌套弹窗）
function openFullReport(i) {
  const s = state.histSessions[i];
  const d = state.sessionDetails[i];
  if (!d) return;
  const rep = Object.assign({}, d.report || {}, { decision: d.decision || {} });
  state.lastResults = d.results || [];  // 兜底：追问按钮 -1 时可取到本场
  const body = document.getElementById('fullreport-body');
  body.innerHTML = `
    <div class="fr-top">
      <button class="ghost fr-back" onclick="backToReport()">← 返回弱点报告</button>
      <div class="fr-head">
        <h2>完整报告</h2>
        <div class="fr-sub">${esc(s.direction_name || '')} · ${esc((s.created_at || '').slice(0, 16))}</div>
      </div>
    </div>
    ${fullReportHTML(rep, d.results || [], i)}`;
  // 切换到完整报告页（不改变顶部 tab 高亮，返回即回到弱点报告）
  $$('.view').forEach(v => v.classList.toggle('active', v.id === 'view-fullreport'));
  const c = document.querySelector('.container');
  if (c) c.scrollTop = 0;
}
function backToReport() {
  showTab('report');
}

function resetInterview() {
  state.sessionId = null;
  document.getElementById('interview-result').style.display = 'none';
  document.getElementById('interview-room').style.display = 'none';
  document.getElementById('interview-setup').style.display = 'block';
  document.getElementById('int-qinfo').style.display = 'none';
  document.getElementById('int-end-btn').style.display = '';
  document.getElementById('int-ai-btn').style.display = '';
  document.getElementById('int-skip-btn').style.display = '';
  renderInterviewEntry();
  checkLiveInterview();
}
function showTab(name) {
  $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  $$('.view').forEach(v => v.classList.toggle('active', v.id === 'view-' + name));
  // 切换标签回到页面顶部，避免停留在上一个页面的滚动位置
  const c = document.querySelector('.container');
  if (c) c.scrollTop = 0;
  // 模拟面试 / 弱点报告页用卡片展示内容，隐藏全局方向侧栏
  const layout = document.querySelector('.app-layout');
  if (layout) layout.classList.toggle('no-sidebar', name === 'interview' || name === 'report');
  if (name === 'interview') {
    // 设置页显示中时刷新当前方向入口
    const setup = document.getElementById('interview-setup');
    if (setup && setup.style.display !== 'none') {
      renderInterviewEntry();
    }
    checkLiveInterview();
  } else if (name === 'report') {
    renderReportDirFilter();
    renderReport();
  }
}

// ================================================================ 弱点报告
let reportDirFilter = 'all';   // 报告方向过滤：'all' 或方向 id

let rpActiveSess = -1;  // 报告左栏当前选中的场次（sessions 索引）

// 报告页方向筛选：可搜索下拉（渲染到左栏顶部），不随刷题库当前方向同步
function renderReportDirFilter() {
  const box = document.getElementById('rp-dirfilter');
  if (!box || !state.data) return;
  const cur = state.data.directions.find(d => d.id === reportDirFilter);
  box.innerHTML = `
    <div class="cs-wrap rp-dirfilter" id="rp-dir-cb">
      <button type="button" class="cs-btn" onclick="toggleReportDirCB()">
        <span class="cs-label" id="rp-dir-cb-label">${cur ? esc(cur.name) : '全部方向'}</span>
        <span class="cs-arrow"></span>
      </button>
      <div class="cs-menu" id="rp-dir-cb-menu">
        <input class="cs-search" id="rp-dir-cb-search" placeholder="搜索方向…"
          oninput="filterReportDir()" onclick="event.stopPropagation()"
          onkeydown="if(event.key==='Escape'){closeReportDirCB();event.stopPropagation();}">
        <div class="cs-opts" id="rp-dir-cb-opts"></div>
      </div>
    </div>`;
  fillReportDirOpts();
}

function fillReportDirOpts() {
  const opts = document.getElementById('rp-dir-cb-opts');
  if (!opts) return;
  const kw = (document.getElementById('rp-dir-cb-search')?.value || '').trim().toLowerCase();
  const dirs = state.data.directions.filter(d => !kw || d.name.toLowerCase().includes(kw));
  if (!dirs.length) { opts.innerHTML = '<div class="cs-empty">没有匹配的方向</div>'; return; }
  // 搜索时不显示"全部方向"（它不匹配关键词）；空搜索才作为默认项置顶
  opts.innerHTML =
    (kw ? '' : `<div class="cs-opt ${reportDirFilter === 'all' ? 'sel' : ''}" onclick="pickReportDir('all')">全部方向</div>`) +
    dirs.map(d =>
      `<div class="cs-opt ${reportDirFilter === d.id ? 'sel' : ''}" data-v="${d.id}" onclick="pickReportDir('${d.id}')">${esc(d.name)}</div>`
    ).join('');
}

function toggleReportDirCB() {
  const w = document.getElementById('rp-dir-cb');
  if (!w) return;
  const opening = !w.classList.contains('open');
  w.classList.toggle('open');
  if (opening) {
    const s = document.getElementById('rp-dir-cb-search');
    if (s) { s.value = ''; fillReportDirOpts(); setTimeout(() => s.focus(), 0); }
  }
}

function filterReportDir() {
  fillReportDirOpts();
}

function pickReportDir(v) {
  reportDirFilter = v;
  closeReportDirCB();
  renderReport();
}

function closeReportDirCB() {
  const w = document.getElementById('rp-dir-cb');
  if (w) w.classList.remove('open');
}

// 点击下拉外部时关闭
document.addEventListener('click', (e) => {
  const w = document.getElementById('rp-dir-cb');
  if (w && w.classList.contains('open') && !w.contains(e.target)) closeReportDirCB();
});

async function renderReport() {
  const body = document.getElementById('report-body');
  let hist;
  try { hist = await apiGet('/api/history'); } catch (e) { body.innerHTML = '<div class="empty">读取历史失败</div>'; return; }
  let sessions = hist.sessions || [];
  // 按方向过滤（全部 / 单个方向）
  if (reportDirFilter && reportDirFilter !== 'all') {
    sessions = sessions.filter(s => (s.direction || '') === reportDirFilter);
  }
  if (!sessions.length) {
    const noDir = reportDirFilter && reportDirFilter !== 'all';
    body.innerHTML = `
      <div class="rp-dirfilter" id="rp-dirfilter"></div>
      <div class="empty-guide">
        <div class="eg-icon">靶</div>
        <h3>${noDir ? '这个方向还没有面试记录' : '还没有模拟面试记录'}</h3>
        <p>${noDir ? '换个方向看看，或完成一场该方向的模拟面试后，弱点报告会自动生成。' : '完成一场模拟面试后，AI 终面官会给出录用判定和逐题复盘，弱点报告会自动生成。'}</p>
        <div class="eg-steps">
          <div class="eg-step"><span class="num">1</span> 去题库刷题熟悉采分点</div>
          <div class="eg-step"><span class="num">2</span> 开始模拟面试</div>
          <div class="eg-step"><span class="num">3</span> 结束后查看录用判定</div>
        </div>
        <div style="margin-top:20px">
          <button onclick="showTab('interview')">去模拟面试</button>
        </div>
      </div>`;
    renderReportDirFilter();
    return;
  }
  state.histSessions = sessions;
  state.sessionDetails = {};  // 预加载所有场次明细（以 sessions 原始索引为准）

  body.innerHTML = '<div class="empty" style="padding:40px 0">正在加载逐题明细…</div>';

  // 并行拉取所有场次明细（个人使用场次量小，直接全量）
  const detailList = await Promise.all(sessions.map(async (s, i) => {
    try { const d = await apiGet('/api/history/' + s.session_id); state.sessionDetails[i] = d; return d; }
    catch (e) { return null; }
  }));

  // 选中最近一场（接口返回旧 → 新）
  rpActiveSess = sessions.length - 1;

  // 左右布局：左栏（方向筛选 + 场次信息流） + 右栏选中场次完整复盘
  body.innerHTML = `<div class="rp-body">
    <div class="rp-sesscol">
      <div class="rp-dirfilter" id="rp-dirfilter"></div>
      <div class="rp-sesslist" id="rp-sesslist"></div>
    </div>
    <div class="rp-detail" id="rp-detail"></div>
  </div>`;
  renderReportDirFilter();
  renderSessList(sessions);
  renderDetail(rpActiveSess, sessions);
}

// 左栏：所有场次（新 → 旧，一眼看全部面试情况）
function renderSessList(sessions) {
  const box = document.getElementById('rp-sesslist');
  if (!box) return;
  const items = [];
  for (let i = sessions.length - 1; i >= 0; i--) items.push(sessItemHTML(sessions[i], i));
  box.innerHTML = items.join('');
}

function sessItemHTML(s, i) {
  const dec = s.decision || {};
  const dcls = ['hire', 'borderline', 'no_hire'].includes(dec.decision) ? dec.decision : 'unknown';
  const dlabel = dec.decision_label || '未判定';
  const isInvalid = !(s.items > 0);
  const on = rpActiveSess === i;
  const time = (s.created_at || '').slice(5, 16);
  const sc = scoreColor(s.total_score);
  return `<div class="rp-sessitem ${on ? 'on' : ''} ${isInvalid ? 'invalid' : ''}" onclick="setActiveSession(${i})">
    <div class="rp-sessitem-top">
      <span class="rp-sessitem-dir">${esc(s.direction_name || '')}</span>
      <span class="rp-sessitem-score" style="color:${sc};background:${scoreSoft(s.total_score)}">${s.total_score}<small>分</small></span>
    </div>
    <div class="rp-sessitem-meta">
      <span class="pill ${dcls}">${esc(dlabel)}</span>
      <span class="rp-sessitem-time">${esc(time)}</span>
      ${isInvalid ? '<span class="rp-invalid-tag">未完成</span>' : ''}
      <button class="rp-sessitem-del" title="删除这场" onclick="event.stopPropagation();deleteSession('${s.session_id}', ${i})">×</button>
    </div>
  </div>`;
}

function scoreColor(score) {
  return score >= 80 ? 'var(--good)' : score >= 60 ? 'var(--warn)' : 'var(--bad)';
}

function scoreSoft(score) {
  return score >= 80 ? 'var(--good-soft)' : score >= 60 ? 'var(--warn-soft)' : 'var(--bad-soft)';
}

// 切换左栏选中场次 → 右栏刷新
function setActiveSession(i) {
  rpActiveSess = i;
  renderSessList(state.histSessions);
  renderDetail(i, state.histSessions);
}

// 右栏：选中场次的完整复盘（标题条 + 逐题完整卡片，弱 → 强）
function renderDetail(i, sessions) {
  const box = document.getElementById('rp-detail');
  if (!box) return;
  const s = sessions[i];
  const d = state.sessionDetails[i];
  const results = (d && d.results) || [];
  const sorted = results.map((r, j) => ({ ...r, _orig: j }))
    .sort((a, b) => (a.score || 0) - (b.score || 0));
  const tsc = scoreColor(s.total_score);
  let html = `<div class="rp-dhead">
    <span class="rp-dhead-ttl">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/><rect x="9" y="3" width="6" height="4" rx="1"/><path d="M9 12h6M9 16h4"/></svg>
      逐题复盘
    </span>
    <span class="rp-dhead-sub">${esc(s.direction_name || '')} · ${esc((s.created_at || '').slice(0, 16))} · ${s.items} 题 · <span style="color:${tsc};font-weight:700">总分 ${s.total_score} 分</span></span>
    <button class="rp-dhead-report" onclick="openFullReport(${i})" title="查看这场面试的完整报告">查看完整报告</button>
  </div>`;
  html += sorted.map(q => reviewCardHTML(q, q._orig, i)).join('');
  if (!sorted.length) html += '<div class="rp-none">这一场没有逐题明细，无法复盘。</div>';
  box.innerHTML = html;
  box.scrollTop = 0;
}


// 单题完整复盘卡片（默认折叠，点击标题展开 我的回答/点评/要点分析）
function reviewCardHTML(q, origIdx, sessIdx) {
  const rc = q.score >= 80 ? 'var(--good)' : q.score >= 60 ? 'var(--warn)' : 'var(--bad)';
  const on = state.userState.mastered.includes(q.question_id);
  const rows = [];
  if (q.key_points && q.key_points.length) rows.push(['kp', '采分点', q.key_points]);
  if (q.missed_points && q.missed_points.length) rows.push(['miss', '遗漏', q.missed_points]);
  if (q.strengths && q.strengths.length) rows.push(['str', '优点', q.strengths]);
  if (q.weaknesses && q.weaknesses.length) rows.push(['weak', '弱点', q.weaknesses]);
  const secs = [];
  if (q.answer) secs.push(`<div class="rv-sec rv-sec-ans" onclick="toggleAnswerSec(this)">
      <div class="rv-sec-label">我的回答 <span class="rv-sec-caret"></span></div>
      <div class="rv-answer">${esc(q.answer)}</div>
    </div>`);
  if (q.comment) secs.push(`<div class="rv-sec">
      <div class="rv-sec-label">面试官点评</div>
      <div class="rv-comment">${esc(q.comment)}</div>
    </div>`);
  if (rows.length) secs.push(`<div class="rv-sec">
      <div class="rv-sec-label">要点分析</div>
      ${rows.map(r => `<div class="rv-tagrow"><span class="rv-taglbl ${r[0]}">${r[1]}</span><div class="rv-tags">${r[2].map(x => `<span class="rv-tag">${esc(x)}</span>`).join('')}</div></div>`).join('')}
    </div>`);
  return `<div class="rvcard">
    <div class="rvcard-head" onclick="toggleMyAnswer(this.closest('.rvcard'))">
      <span class="rvcard-score" style="color:${rc};background:${scoreSoft(q.score)}">${q.score}<small>分</small></span>
      <span class="rvcard-q">${esc(q.question || '')}</span>
    </div>
    <div class="rv-open">
      ${secs.join('')}
      <div class="rvcard-foot">
        ${q.question_id ? `<button class="q-btn ${on ? 'on' : ''}" onclick="toggleMasteredQuick('${q.question_id}', this)"><svg class="rv-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg><span class="rv-btn-label">${on ? '已掌握' : '标记掌握'}</span></button>` : ''}
        <button class="ghost" onclick="openReview(${origIdx}, ${sessIdx})"><svg class="rv-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>追问这题</button>
      </div>
    </div>
  </div>`;
}

// 卡片折叠：点击标题展开/收起整卡
function toggleMyAnswer(card) {
  card.classList.toggle('open');
}

// 我的回答二次展开
function toggleAnswerSec(sec) {
  sec.classList.toggle('open');
}

async function deleteSession(sid, idx) {
  showConfirm('确定删除这场面试记录？删除后不可恢复。', async () => {
    try {
      await apiPost('/api/history/delete', { session_id: sid });
      toast('已删除');
      renderReport();
    } catch (e) {
      toast('删除失败：' + e.message);
    }
  }, '删除');
}

// ================================================================ 复盘追问对话
let rv = { idx: -1, messages: [] };

function openReview(qIdx, sessIdx) {
  // 打开新题前中止上一个复盘流式，避免旧回复写入污染
  if (reviewAbort) { reviewAbort.abort(); reviewAbort = null; }
  setReviewSendBtn('send');
  let results = null, src = '';
  if (sessIdx >= 0) {
    if (state.sessionDetails && state.sessionDetails[sessIdx]) {
      results = state.sessionDetails[sessIdx].results || [];
      src = '历史场次';
    }
  }
  if (!results || !results.length) {
    results = state.lastResults || [];
    src = '本场结果';
  }
  const r = results[qIdx];
  if (!r) { toast(`没有这道题的数据（${src || '无来源'}，共 ${results.length} 题，要取第 ${qIdx + 1} 题）`); return; }
  rv.qIdx = qIdx;
  rv.results = results;
  rv.messages = [];
  // 我的回答按 候选人/面试官 拆成气泡，增加阅读层次
  const rvChatBubbles = (text) => String(text || '').split(/(?=(?:候选人|面试官)[:：])/).map(p => {
    const m = p.match(/^(候选人|面试官)([:：])/);
    if (!m) return { role: 'plain', text: p };
    return { role: m[1], text: p.slice(m[0].length) };
  }).filter(x => x.text.trim()).map(x => {
    if (x.role === '候选人') return `<div class="rv-bubble me">${esc(x.text)}</div>`;
    if (x.role === '面试官') return `<div class="rv-bubble ai">${esc(x.text)}</div>`;
    return `<div class="rv-bubble plain">${esc(x.text)}</div>`;
  }).join('');
  const sumBox = (label, cls, points) => points && points.length
    ? `<div class="rv-sum-box"><div class="rv-sec-label ${cls}">${label}</div><ul class="rv-plist">${points.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>`
    : '';
  document.getElementById('rv-summary').innerHTML = sumBox('遗漏的点', 'miss', r.missed_points) + sumBox('弱点', 'weak', r.weaknesses);
  document.getElementById('rv-ctx').innerHTML = `
    <div class="rv-q">${esc(r.question || '')}</div>
    <div style="font-size:12px;color:var(--dim);margin-bottom:8px">${esc(r.topic || '')} · 得分 ${r.score}</div>
    <div class="rv-sec-label">我的回答</div>
    <div class="rv-a">${rvChatBubbles(r.answer)}</div>`;
  document.getElementById('rv-chat').innerHTML = '';
  addRvChat('ai', '这道题你拿了 ' + r.score + ' 分。想深挖哪个点，还是觉得评分不对？说理由，我跟你掰扯。');
  document.getElementById('review-modal').classList.add('show');
  document.getElementById('review-mini').classList.remove('show');
  document.getElementById('rv-input').focus();
}
function closeReview() {
  if (reviewAbort) { reviewAbort.abort(); reviewAbort = null; }
  setReviewSendBtn('send');
  document.getElementById('review-modal').classList.remove('show');
  document.getElementById('review-mini').classList.remove('show');
  rv.messages = [];
}

// 最小化复盘追问：收成底部悬浮条，可暂时去干别的（正在生成的回复不中断）
function minimizeReview() {
  if (rv.qIdx < 0) return;
  document.getElementById('review-modal').classList.remove('show');
  lockBodyScroll(false);
  const r = (rv.results || [])[rv.qIdx] || {};
  const t = r.question || '复盘追问';
  document.getElementById('review-mini-title').textContent = t.length > 30 ? t.slice(0, 30) + '…' : t;
  const mini = document.getElementById('review-mini');
  mini.classList.add('show');
  document.getElementById('minis-stack').appendChild(mini); // 追加到末尾 → 堆叠在最上
}
function restoreReview() {
  document.getElementById('review-mini').classList.remove('show');
  if (rv.qIdx < 0) return;
  document.getElementById('review-modal').classList.add('show');
  lockBodyScroll(true);
  const chat = document.getElementById('rv-chat');
  if (chat) chat.scrollTop = chat.scrollHeight;
  const inp = document.getElementById('rv-input');
  if (inp) inp.focus();
}
function addRvChat(role, text) {
  const box = document.getElementById('rv-chat');
  const div = document.createElement('div');
  div.className = 'msg ' + (role === 'ai' ? 'ai' : 'me');
  div.innerHTML = role === 'ai' ? '<b>导师</b>：' + esc(text) : esc(text);
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}
let reviewAbort = null;  // 复盘追问流式的 AbortController
// 切换发送按钮图标：send=纸飞机，stop=停止（与追问/面试一致）
function setReviewSendBtn(mode) {
  const btn = document.getElementById('rv-send');
  const sendI = btn.querySelector('.send-icon');
  const stopI = btn.querySelector('.stop-icon');
  if (mode === 'stop') {
    btn.classList.add('stopping');
    btn.title = '停止生成';
    if (sendI) sendI.style.display = 'none';
    if (stopI) stopI.style.display = '';
  } else {
    btn.classList.remove('stopping');
    btn.title = '发送';
    if (sendI) sendI.style.display = '';
    if (stopI) stopI.style.display = 'none';
  }
}
async function sendReview() {
  if (reviewAbort) { reviewAbort.abort(); return; }
  const input = document.getElementById('rv-input');
  const text = input.value.trim();
  if (!text) { toast('先说点什么'); return; }
  const r = (rv.results || [])[rv.qIdx] || {};
  setReviewSendBtn('stop');
  addRvChat('me', text);
  input.value = '';
  input.style.height = 'auto';
  rv.messages.push({ role: 'user', text });
  const bubble = createStreamBubble('rv-chat', 'ai', '导师思考中');
  let full = '', errMsg = null, aborted = false;
  const ctrl = new AbortController();
  reviewAbort = ctrl;
  try {
    await apiStream('/api/review/chat/stream', {
      question: r.question, my_answer: r.answer, score: r.score, comment: r.comment,
      messages: rv.messages,
    }, (ev, p) => {
      if (ev === 'reasoning') bubble.reasoning(p.t || '');
      else if (ev === 'content') { full += (p.t || ''); bubble.content(p.t || ''); }
      else if (ev === 'error') errMsg = p.message || '未知错误';
    }, ctrl.signal);
  } catch (e) {
    if (e && e.name === 'AbortError') aborted = true;
    else errMsg = e.message;
  } finally {
    reviewAbort = null;
    setReviewSendBtn('send');
  }
  if (aborted) {
    bubble.remove();
  } else if (errMsg || !full) {
    bubble.setText('追问失败：' + (errMsg || '没有回应，再试一次'));
  } else {
    rv.messages.push({ role: 'assistant', text: full });
  }
  input.focus();
}
// 复盘输入框自动增高（与追问/面试一致）
(function () {
  const ta = document.getElementById('rv-input');
  if (!ta) return;
  ta.addEventListener('input', () => {
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 160) + 'px';
  });
})();

// ================================================================ 快捷键：Enter 发送
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' || e.shiftKey) return;
  if (e.target && e.target.id === 'int-answer') { e.preventDefault(); submitAnswer(); }
  else if (e.target && e.target.id === 'rv-input') { e.preventDefault(); sendReview(); }
});

// ================================================================ 启动
(async function init() {
  try { await initBank(); } catch (e) { toast('加载题库失败：' + e.message); }
  checkLiveInterview();
})();
