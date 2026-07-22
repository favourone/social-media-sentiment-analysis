let csrfToken = '';
let toastTimer = null;

const escapeHTML = (value) => String(value ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#039;');

function notify(message, isError = false) {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.className = `toast show${isError ? ' error' : ''}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast.className = 'toast'; }, 3500);
}

async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
    if (options.method && options.method !== 'GET' && csrfToken) headers.set('X-CSRF-Token', csrfToken);
    const response = await fetch(path, {...options, headers});
    let payload;
    try { payload = await response.json(); } catch (_) { payload = null; }
    if (response.status === 401) {
        window.location.href = '/login';
        throw new Error('登录已失效');
    }
    if (!response.ok || !payload?.ok) throw new Error(payload?.error?.message || `请求失败 (${response.status})`);
    return payload.data;
}

const fmtTime = (value) => value ? new Date(value).toLocaleString('zh-CN', {hour12: false}) : '—';
const statusName = {pending: '等待中', running: '进行中', paused: '已暂停', succeeded: '已完成', failed: '失败', cancelled: '已取消'};
const statusBadge = (status) => `<span class="status ${escapeHTML(status)}">${escapeHTML(statusName[status] || status)}</span>`;

function activateSection(name) {
    document.querySelectorAll('.nav-link').forEach((item) => item.classList.toggle('active', item.dataset.section === name));
    document.querySelectorAll('.page').forEach((item) => item.classList.toggle('active', item.id === `section-${name}`));
    history.replaceState(null, '', `#${name}`);
}

async function loadSession() {
    const data = await api('/api/v1/auth/me');
    csrfToken = data.csrf_token;
}

async function loadSettings() {
    const [settings, ready] = await Promise.all([
        api('/api/v1/settings/status'),
        fetch('/api/v1/health/ready').then((response) => response.json()).then((payload) => payload.data)
    ]);
    document.getElementById('queue-mode').textContent = `${settings.queue.mode} · ${settings.queue.ready ? '可用' : '不可用'}`;
    document.getElementById('collector-state').textContent = settings.collector.installed && settings.collector.command_configured ? '已配置' : '待配置';
    document.getElementById('license-state').textContent = settings.collector.license_accepted ? '已确认非商业用途' : '尚未确认';
    const badge = document.getElementById('ready-badge');
    badge.textContent = ready.status === 'ready' ? '运行就绪' : '配置待完善';
    badge.className = `status ${ready.status === 'ready' ? 'succeeded' : 'paused'}`;
    document.getElementById('settings-list').innerHTML = `
        <div><dt>管理员</dt><dd>${escapeHTML(settings.admin_username)}</dd></div>
        <div><dt>任务模式</dt><dd>${escapeHTML(settings.queue.mode)}</dd></div>
        <div><dt>数据库</dt><dd>${escapeHTML(settings.database_path)}</dd></div>
        <div><dt>采集平台</dt><dd>微博</dd></div>
        <div><dt>商业授权</dt><dd>未包含</dd></div>`;
}

function jobDetail(job) {
    if (job.error_message) return `<strong>${escapeHTML(job.error_code || 'error')}</strong><small>${escapeHTML(job.error_message)}</small>`;
    const stats = job.stats || {};
    if (job.status === 'succeeded') return `<strong>入库 ${stats.inserted_posts || 0} 条</strong><small>重复 ${stats.duplicate_posts || 0} · 拒绝 ${stats.rejected_records || 0}</small>`;
    return '<small>等待任务更新</small>';
}

async function loadCollection() {
    const jobs = await api('/api/v1/collection-jobs');
    document.getElementById('metric-collection').textContent = jobs.length;
    document.getElementById('metric-posts').textContent = jobs.reduce((sum, job) => sum + (job.stats?.inserted_posts || 0), 0);
    document.getElementById('collection-rows').innerHTML = jobs.length ? jobs.map((job) => `
        <tr><td>${fmtTime(job.created_at)}</td><td>${escapeHTML(job.adapter === 'file' ? '文件导入' : '微博采集')}</td>
        <td>${escapeHTML(job.keywords.join('、'))}</td><td>${statusBadge(job.status)}</td>
        <td>${job.progress}%</td><td>${jobDetail(job)}</td></tr>`).join('') : '<tr><td colspan="6" class="empty-cell">还没有采集任务</td></tr>';
}

async function loadAnalysis() {
    const jobs = await api('/api/v1/analysis-jobs');
    document.getElementById('metric-analysis').textContent = jobs.length;
    document.getElementById('analysis-rows').innerHTML = jobs.length ? jobs.map((job) => {
        const summary = job.result?.summary;
        const detail = job.error_message ? `<strong>${escapeHTML(job.error_code)}</strong><small>${escapeHTML(job.error_message)}</small>` : summary ? `<strong>${summary.sample_count} 条样本</strong><small>负面筛查 ${summary.negative_ratio}%</small>` : '<small>等待任务更新</small>';
        return `<tr><td>${fmtTime(job.created_at)}</td><td>${escapeHTML(job.analysis_type)}</td><td>${escapeHTML(job.params.topic || '全部')}</td><td>${statusBadge(job.status)}</td><td>${summary?.sample_count ?? '—'}</td><td>${detail}</td></tr>`;
    }).join('') : '<tr><td colspan="6" class="empty-cell">还没有分析任务</td></tr>';
}

async function loadAlerts() {
    const alerts = await api('/api/v1/alerts');
    document.getElementById('metric-alerts').textContent = alerts.length;
    document.getElementById('alert-list').innerHTML = alerts.length ? alerts.map((alert) => `
        <article class="alert-card ${escapeHTML(alert.level)}"><span class="alert-bar"></span><div><h3>${escapeHTML(alert.topic)}</h3><p>${escapeHTML(alert.message)}</p></div><div>${statusBadge(alert.level === 'high' ? 'failed' : 'paused')}<small>${fmtTime(alert.created_at)}</small></div></article>`).join('') : '<article class="panel"><p class="muted">当前没有达到阈值的预警。</p></article>';
}

async function loadReports() {
    const reports = await api('/api/v1/reports');
    document.getElementById('report-rows').innerHTML = reports.length ? reports.map((report) => {
        let action = report.error_message ? `<strong>${escapeHTML(report.error_code)}</strong><small>${escapeHTML(report.error_message)}</small>` : '<small>等待生成</small>';
        if (report.status === 'succeeded') action = `<a class="button secondary" href="/api/v1/reports/${encodeURIComponent(report.id)}.${report.format}">下载</a>`;
        return `<tr><td>${fmtTime(report.created_at)}</td><td>${escapeHTML(report.format.toUpperCase())}</td><td>${escapeHTML(report.filters.topic || '全部')}</td><td>${statusBadge(report.status)}</td><td>${report.sample_count}</td><td>${action}</td></tr>`;
    }).join('') : '<tr><td colspan="6" class="empty-cell">还没有导出报告</td></tr>';
}

async function refreshAll() {
    const operations = [loadSettings(), loadCollection(), loadAnalysis(), loadAlerts(), loadReports()];
    const results = await Promise.allSettled(operations);
    const failure = results.find((item) => item.status === 'rejected');
    if (failure) notify(failure.reason.message, true);
}

document.querySelectorAll('.nav-link').forEach((button) => button.addEventListener('click', () => activateSection(button.dataset.section)));
document.querySelectorAll('.refresh').forEach((button) => button.addEventListener('click', () => ({collection: loadCollection, analysis: loadAnalysis, reports: loadReports}[button.dataset.refresh]()).catch((error) => notify(error.message, true))));

document.getElementById('collection-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
        await api('/api/v1/collection-jobs', {method: 'POST', body: JSON.stringify({keywords: form.get('keywords'), max_pages: Number(form.get('max_pages'))})});
        notify('采集任务已创建'); event.currentTarget.reset(); await loadCollection();
    } catch (error) { notify(error.message, true); }
});

document.getElementById('import-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
        await api('/api/v1/imports', {method: 'POST', body: new FormData(event.currentTarget)});
        notify('数据已提交导入'); event.currentTarget.reset(); await loadCollection();
    } catch (error) { notify(error.message, true); }
});

for (const [formId, path] of [['analysis-form', '/api/v1/analysis-jobs'], ['report-form', '/api/v1/reports']]) {
    document.getElementById(formId).addEventListener('submit', async (event) => {
        event.preventDefault();
        const payload = Object.fromEntries(new FormData(event.currentTarget));
        try { await api(path, {method: 'POST', body: JSON.stringify(payload)}); notify('任务已创建'); await refreshAll(); }
        catch (error) { notify(error.message, true); }
    });
}

document.getElementById('password-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    try { await api('/api/v1/auth/password', {method: 'POST', body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget)))}); window.location.href = '/login'; }
    catch (error) { notify(error.message, true); }
});

document.getElementById('logout').addEventListener('click', async () => {
    try { await api('/api/v1/auth/logout', {method: 'POST'}); } finally { window.location.href = '/login'; }
});

(async () => {
    try {
        await loadSession();
        activateSection(location.hash.slice(1) || 'overview');
        await refreshAll();
        setInterval(refreshAll, 5000);
    } catch (error) { notify(error.message, true); }
})();
