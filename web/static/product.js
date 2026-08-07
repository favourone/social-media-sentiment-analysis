'use strict';

let csrfToken = '';
let toastTimer = null;
let monitors = [];
let refreshTimer = null;

const statusNames = {
    pending: '等待中',
    running: '运行中',
    paused: '已暂停',
    succeeded: '已完成',
    failed: '失败',
    cancelled: '已取消',
    active: '已启用',
    archived: '已归档',
    new: '新预警',
    acknowledged: '已确认',
    investigating: '调查中',
    resolved: '已解决',
    false_positive: '误报',
    open: '持续关注',
    watching: '观察中'
};
const trendNames = {
    rising: '上升',
    emerging: '新出现',
    declining: '下降',
    stable: '平稳'
};
const severityNames = {
    critical: '严重',
    high: '高风险',
    medium: '中风险',
    low: '低风险'
};
const kindNames = {
    risk_keyword: '风险词命中',
    negative_ratio: '负面比例',
    volume_spike: '讨论量突增'
};

const escapeHTML = (value) => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');

const fmtTime = (value) => {
    if (!value) return '—';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime())
        ? escapeHTML(value)
        : parsed.toLocaleString('zh-CN', {hour12: false});
};

const fmtNumber = (value) => Number(value || 0).toLocaleString('zh-CN');

function safeHref(value) {
    try {
        const url = new URL(String(value || ''), window.location.origin);
        return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
    } catch (_) {
        return '';
    }
}

function notify(message, isError = false) {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.className = `toast show${isError ? ' error' : ''}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast.className = 'toast'; }, 3800);
}

async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body && !(options.body instanceof FormData)) {
        headers.set('Content-Type', 'application/json');
    }
    if (options.method && options.method !== 'GET' && csrfToken) {
        headers.set('X-CSRF-Token', csrfToken);
    }
    const response = await fetch(path, {...options, headers});
    let payload = null;
    try {
        payload = await response.json();
    } catch (_) {
        throw new Error(`服务返回了无法识别的响应 (${response.status})`);
    }
    if (response.status === 401) {
        window.location.href = '/login';
        throw new Error('登录已失效');
    }
    if (!response.ok || !payload?.ok) {
        throw new Error(payload?.error?.message || `请求失败 (${response.status})`);
    }
    return payload.data;
}

function statusBadge(status) {
    return `<span class="status ${escapeHTML(status)}">${escapeHTML(statusNames[status] || status || '未知')}</span>`;
}

function severityBadge(severity) {
    return `<span class="severity ${escapeHTML(severity)}">${escapeHTML(severityNames[severity] || severity)}</span>`;
}

function empty(message) {
    return `<div class="empty-state">${escapeHTML(message)}</div>`;
}

function splitTerms(value) {
    return String(value || '')
        .replaceAll('，', ',')
        .split(/[,\n]/)
        .map((item) => item.trim())
        .filter(Boolean);
}

function activateSection(name) {
    const target = document.getElementById(`section-${name}`);
    if (!target) name = 'overview';
    document.querySelectorAll('.nav-link').forEach((item) => {
        item.classList.toggle('active', item.dataset.section === name);
    });
    document.querySelectorAll('.page').forEach((item) => {
        item.classList.toggle('active', item.id === `section-${name}`);
    });
    history.replaceState(null, '', `#${name}`);
    const loader = {
        overview: loadOverview,
        monitors: loadMonitors,
        signals: loadSignals,
        events: loadEvents,
        alerts: loadAlerts,
        analytics: loadAnalytics,
        data: loadCollection,
        reports: loadReports,
        settings: loadSettings
    }[name];
    if (loader) loader().catch((error) => notify(error.message, true));
}

async function loadSession() {
    const data = await api('/api/v1/auth/me');
    csrfToken = data.csrf_token;
}

function monitorName(id) {
    return monitors.find((item) => item.id === id)?.name || '未知项目';
}

function populateMonitorSelects() {
    document.querySelectorAll('.monitor-select').forEach((select) => {
        const previous = select.value;
        const allowAll = select.closest('#alert-filter') !== null;
        select.innerHTML = [
            allowAll ? '<option value="">全部项目</option>' : '',
            ...monitors.map((item) => (
                `<option value="${escapeHTML(item.id)}">${escapeHTML(item.name)}</option>`
            ))
        ].join('');
        if ([...select.options].some((option) => option.value === previous)) {
            select.value = previous;
        }
    });
}

function sourceLabels(monitor) {
    return (monitor.sources || []).map((source) => {
        const label = source.kind === 'database'
            ? '数据仓'
            : source.kind === 'rss' ? 'RSS' : '微博';
        return `<span class="source-tag">${escapeHTML(label)}</span>`;
    }).join('');
}

function runFailureHTML(run) {
    if (!run || !['paused', 'failed'].includes(run.status)) return '';
    const sourceError = run.stats?.source_errors?.[0];
    const code = sourceError?.code || run.error_code || 'run_failed';
    const message = sourceError?.message || run.error_message || '最近一次运行未成功完成';
    return `<div class="notice error">
        <strong>${escapeHTML(code)}</strong> · ${escapeHTML(message)}
    </div>`;
}

async function loadMonitors() {
    const [monitorData, runs] = await Promise.all([
        api('/api/v2/monitors'),
        api('/api/v2/monitor-runs?limit=200')
    ]);
    monitors = monitorData;
    const latestRunByMonitor = new Map();
    for (const run of runs) {
        if (!latestRunByMonitor.has(run.monitor_id)) {
            latestRunByMonitor.set(run.monitor_id, run);
        }
    }
    populateMonitorSelects();
    const host = document.getElementById('monitor-list');
    host.innerHTML = monitors.length ? monitors.map((monitor) => {
        const latestRun = latestRunByMonitor.get(monitor.id);
        const terms = [
            ...(monitor.keywords || []).map((term) => `<span class="chip">${escapeHTML(term)}</span>`),
            ...(monitor.risk_terms || []).map((term) => `<span class="chip risk">${escapeHTML(term)}</span>`)
        ].join('');
        const action = monitor.status === 'active'
            ? `<button class="ghost" data-action="monitor-status" data-id="${escapeHTML(monitor.id)}" data-status="paused">暂停</button>`
            : `<button class="secondary" data-action="monitor-status" data-id="${escapeHTML(monitor.id)}" data-status="active">启用</button>`;
        return `<article class="monitor-card ${escapeHTML(monitor.status)}">
            <div class="monitor-head">
                <div>
                    <h3>${escapeHTML(monitor.name)}</h3>
                    <div class="monitor-meta">
                        ${statusBadge(monitor.status)}
                        <span>每 ${escapeHTML(monitor.interval_minutes)} 分钟</span>
                        <span>下次 ${fmtTime(monitor.next_run_at)}</span>
                    </div>
                </div>
                <span>${sourceLabels(monitor)}</span>
            </div>
            <p>${escapeHTML(monitor.description || '未填写监测说明')}</p>
            <div class="monitor-meta">${terms || '<span class="muted">未配置展示词</span>'}</div>
            <div class="monitor-meta">
                <span>负面阈值 ${escapeHTML(monitor.negative_threshold)}%</span>
                <span>突增阈值 ${escapeHTML(monitor.spike_threshold)} 条</span>
                <span>最近运行 ${fmtTime(monitor.last_run_at)}</span>
            </div>
            ${runFailureHTML(latestRun)}
            <div class="card-actions">
                ${monitor.status === 'active' ? `<button class="primary" data-action="run-monitor" data-id="${escapeHTML(monitor.id)}">立即运行</button>` : ''}
                <button class="secondary" data-action="open-monitor-analytics" data-id="${escapeHTML(monitor.id)}">分析实验室</button>
                <button class="ghost" data-action="open-monitor-signals" data-id="${escapeHTML(monitor.id)}">查看信号</button>
                <button class="ghost" data-action="open-monitor-events" data-id="${escapeHTML(monitor.id)}">查看事件</button>
                ${action}
            </div>
        </article>`;
    }).join('') : empty('还没有监测项目。先在左侧定义一个可核验的关注范围。');
    return monitors;
}

function eventRank(event, index) {
    const metrics = event.metrics || {};
    return `<div class="rank-item">
        <span class="rank-number">${String(index + 1).padStart(2, '0')}</span>
        <div>
            <h3>${escapeHTML(event.title)}</h3>
            <p>${fmtNumber(metrics.sample_count)} 条信号 · 负面 ${escapeHTML(metrics.negative_ratio || 0)}% · ${escapeHTML(trendNames[metrics.trend] || '未知趋势')}</p>
        </div>
        <button class="text-button" data-action="view-event" data-id="${escapeHTML(event.id)}">热度 ${escapeHTML(metrics.heat_score || 0)}</button>
    </div>`;
}

function alertCompact(alert) {
    return `<div class="compact-item">
        <div>
            <h3>${escapeHTML(alert.title)}</h3>
            <p>${escapeHTML(alert.monitor_name)} · ${fmtTime(alert.last_seen)}</p>
        </div>
        ${severityBadge(alert.severity)}
    </div>`;
}

function runCompact(run) {
    const stats = run.stats || {};
    return `<div class="run-item ${escapeHTML(run.status)}">
        <strong>${escapeHTML(monitorName(run.monitor_id))}</strong>
        <small>${statusBadge(run.status)} · ${escapeHTML(run.progress)}%</small>
        <small>${fmtNumber(stats.matched_signals)} 信号 / ${fmtNumber(stats.events)} 事件</small>
        ${run.error_message ? `<small title="${escapeHTML(run.error_message)}">${escapeHTML(run.error_code || run.error_message)}</small>` : ''}
    </div>`;
}

async function loadOverview() {
    const data = await api('/api/v2/overview');
    document.getElementById('metric-monitors').textContent = fmtNumber(data.active_monitors);
    document.getElementById('metric-signals').textContent = fmtNumber(data.signals);
    document.getElementById('metric-events').textContent = fmtNumber(data.events);
    document.getElementById('metric-alerts').textContent = fmtNumber(data.open_alerts);
    document.getElementById('metric-high-alerts').textContent = `${fmtNumber(data.high_alerts)} 条高风险`;
    document.getElementById('overview-events').innerHTML = data.top_events.length
        ? data.top_events.map(eventRank).join('')
        : empty('运行监测项目后，重点事件会出现在这里。');
    document.getElementById('overview-alerts').innerHTML = data.recent_alerts.length
        ? data.recent_alerts.map(alertCompact).join('')
        : empty('当前没有待处置预警。');
    document.getElementById('overview-runs').innerHTML = data.recent_runs.length
        ? data.recent_runs.map(runCompact).join('')
        : empty('还没有监测运行记录。');
    await loadCompetitionStory();
}

async function loadSignals() {
    const form = document.getElementById('signal-filter');
    const monitorId = new FormData(form).get('monitor_id') || monitors[0]?.id;
    const host = document.getElementById('signal-list');
    if (!monitorId) {
        host.innerHTML = empty('请先创建监测项目。');
        return;
    }
    form.elements.monitor_id.value = monitorId;
    const values = new FormData(form);
    const query = new URLSearchParams({monitor_id: monitorId, limit: '100'});
    for (const key of ['q', 'platform', 'sentiment']) {
        if (values.get(key) !== '') query.set(key, values.get(key));
    }
    const signals = await api(`/api/v2/signals?${query}`);
    if (!signals.length) {
        const runs = await api(
            `/api/v2/monitor-runs?monitor_id=${encodeURIComponent(monitorId)}&limit=1`
        );
        const failure = runFailureHTML(runs[0]);
        host.innerHTML = failure || empty('当前筛选条件下没有匹配信号。');
        return;
    }
    host.innerHTML = signals.map((signal) => {
        const url = safeHref(signal.source_url);
        const tags = [
            ...(signal.matched_terms || []).map((term) => `<span class="chip">${escapeHTML(term)}</span>`),
            ...(signal.risk_terms || []).map((term) => `<span class="chip risk">${escapeHTML(term)}</span>`)
        ].join('');
        return `<article class="signal-card">
            <div>
                <div class="signal-head">
                    <div class="signal-meta">
                        <span class="source-tag">${escapeHTML(signal.platform)}</span>
                        <span>${escapeHTML(signal.author || '未知作者')}</span>
                        <span>${fmtTime(signal.published_at || signal.fetched_at)}</span>
                        <span>${signal.sentiment === 0 ? '负面筛查' : '非负面'}</span>
                    </div>
                </div>
                <p class="signal-text">${escapeHTML(signal.text)}</p>
                <div class="signal-meta">${tags}${signal.event_title ? `<span>事件：${escapeHTML(signal.event_title)}</span>` : ''}</div>
                ${url ? `<div class="signal-actions"><a class="button ghost" href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">核验原文 ↗</a></div>` : ''}
            </div>
            <div class="signal-score"><small>匹配度</small><br>${escapeHTML(signal.relevance_score)}<small>%</small></div>
        </article>`;
    }).join('');
}

function eventCard(event) {
    const metrics = event.metrics || {};
    const topTerms = (metrics.top_terms || []).slice(0, 5)
        .map((term) => `<span class="chip">${escapeHTML(term)}</span>`).join('');
    return `<article class="event-card">
        <div class="event-top">
            <div>
                <div class="event-meta">${statusBadge(event.status)} <span class="trend ${escapeHTML(metrics.trend)}">${escapeHTML(trendNames[metrics.trend] || '未知')}</span></div>
                <h3>${escapeHTML(event.title)}</h3>
            </div>
            <span class="heat-score">${escapeHTML(metrics.heat_score || 0)}<small>HEAT</small></span>
        </div>
        <p>${escapeHTML(event.summary)}</p>
        <div class="event-metrics">
            <span><strong>${fmtNumber(metrics.sample_count)}</strong>信号</span>
            <span><strong>${escapeHTML(metrics.negative_ratio || 0)}%</strong>负面</span>
            <span><strong>${fmtNumber(metrics.source_count)}</strong>来源</span>
        </div>
        <div class="event-meta">${topTerms}</div>
        <div class="card-actions">
            <button class="primary" data-action="view-event" data-id="${escapeHTML(event.id)}">打开证据</button>
            <button class="secondary" data-action="create-brief" data-id="${escapeHTML(event.id)}">生成简报</button>
        </div>
    </article>`;
}

async function loadEvents() {
    const select = document.getElementById('event-monitor');
    const monitorId = select.value || monitors[0]?.id;
    const host = document.getElementById('event-list');
    if (!monitorId) {
        host.innerHTML = empty('请先创建并运行监测项目。');
        return;
    }
    select.value = monitorId;
    const events = await api(`/api/v2/events?monitor_id=${encodeURIComponent(monitorId)}`);
    host.innerHTML = events.length
        ? events.map(eventCard).join('')
        : empty('该项目尚未形成事件。运行监测后，相关信号会在这里聚合。');
}

function briefHTML(brief) {
    if (!brief) return '<p class="muted">尚未生成事件简报。</p>';
    if (brief.status !== 'succeeded') {
        const message = brief.error_message || `简报${statusNames[brief.status] || brief.status}`;
        return `<div class="brief-box"><h4>事件简报</h4><p>${escapeHTML(message)}</p></div>`;
    }
    const result = brief.result || {};
    const list = (items) => (items || []).map((item) => `<li>${escapeHTML(item)}</li>`).join('');
    return `<div class="brief-box">
        <h4>${escapeHTML(result.headline || '事件简报')}</h4>
        <p>${escapeHTML(result.summary || '')}</p>
        <strong>关键判断</strong><ul>${list(result.key_points)}</ul>
        ${(result.risks || []).length ? `<strong>风险</strong><ul>${list(result.risks)}</ul>` : ''}
        <strong>建议动作</strong><ul>${list(result.recommended_actions)}</ul>
        <p><strong>不确定性：</strong>${escapeHTML(result.uncertainty || '—')}</p>
        <p class="help">模式：${escapeHTML(result.mode || brief.mode)} · 引用信号：${escapeHTML((result.citations || []).join('、') || '—')}</p>
    </div>`;
}

async function openEvent(eventId) {
    const event = await api(`/api/v2/events/${encodeURIComponent(eventId)}`);
    const metrics = event.metrics || {};
    const visualizationDetail = typeof competitionEventDetailHTML === 'function'
        ? competitionEventDetailHTML(eventId)
        : '';
    const signals = (event.signals || []).map((signal) => {
        const url = safeHref(signal.source_url);
        return `<article class="evidence-item">
            <div class="signal-meta"><strong>#${escapeHTML(signal.id)}</strong><span>${escapeHTML(signal.platform)}</span><span>${fmtTime(signal.published_at || signal.fetched_at)}</span></div>
            <p>${escapeHTML(signal.text)}</p>
            ${url ? `<a class="text-button" href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">核验原文 ↗</a>` : ''}
        </article>`;
    }).join('');
    document.getElementById('event-detail').innerHTML = `
        <p class="eyebrow">EVIDENCE BUNDLE</p>
        <h2>${escapeHTML(event.title)}</h2>
        <p class="muted">${escapeHTML(event.summary)}</p>
        ${visualizationDetail ? '' : `<div class="event-metrics">
            <span><strong>${fmtNumber(metrics.sample_count)}</strong>信号</span>
            <span><strong>${escapeHTML(metrics.negative_ratio || 0)}%</strong>负面筛查</span>
            <span><strong>${escapeHTML(metrics.heat_score || 0)}</strong>热度</span>
        </div>`}
        ${visualizationDetail}
        ${briefHTML(event.latest_brief)}
        <div class="card-actions"><button class="secondary" data-action="create-brief" data-id="${escapeHTML(event.id)}">重新生成简报</button></div>
        <h3>原始证据</h3>
        <div class="evidence-list">${signals || empty('该事件没有可展示证据。')}</div>`;
    document.getElementById('event-dialog').showModal();
}

function alertCard(alert) {
    const evidence = alert.evidence || {};
    const actions = {
        new: [
            ['acknowledged', '确认收到'],
            ['investigating', '开始调查'],
            ['false_positive', '标记误报']
        ],
        acknowledged: [
            ['investigating', '开始调查'],
            ['resolved', '解决']
        ],
        investigating: [
            ['resolved', '解决'],
            ['false_positive', '标记误报']
        ],
        resolved: [['new', '重新打开']],
        false_positive: [['new', '重新打开']]
    }[alert.status] || [];
    return `<article class="incident-card ${escapeHTML(alert.severity)} ${escapeHTML(alert.status)}">
        <div class="incident-head">
            <div>
                <div class="incident-meta">${severityBadge(alert.severity)} ${statusBadge(alert.status)} <span>${escapeHTML(kindNames[alert.kind] || alert.kind)}</span></div>
                <h3>${escapeHTML(alert.title)}</h3>
            </div>
            <span class="muted">${fmtTime(alert.last_seen)}</span>
        </div>
        <p>${escapeHTML(alert.message)}</p>
        <div class="incident-meta">
            <span>项目：${escapeHTML(alert.monitor_name)}</span>
            <span>样本：${fmtNumber(evidence.sample_count)}</span>
            <span>置信提示：${escapeHTML(evidence.confidence || '—')}</span>
        </div>
        <div class="incident-actions">
            ${alert.event_id ? `<button class="ghost" data-action="view-event" data-id="${escapeHTML(alert.event_id)}">查看证据</button>` : ''}
            ${actions.map(([status, label]) => `<button class="${status === 'false_positive' ? 'ghost' : 'secondary'}" data-action="transition-alert" data-id="${escapeHTML(alert.id)}" data-status="${escapeHTML(status)}">${escapeHTML(label)}</button>`).join('')}
        </div>
    </article>`;
}

async function loadAlerts() {
    const values = new FormData(document.getElementById('alert-filter'));
    const query = new URLSearchParams();
    if (values.get('monitor_id')) query.set('monitor_id', values.get('monitor_id'));
    if (values.get('status')) query.set('status', values.get('status'));
    const alerts = await api(`/api/v2/alerts?${query}`);
    document.getElementById('incident-list').innerHTML = alerts.length
        ? alerts.map(alertCard).join('')
        : empty('当前筛选条件下没有预警。');
}

function collectionDetail(job) {
    if (job.error_message) {
        return `<strong>${escapeHTML(job.error_code || 'error')}</strong><small>${escapeHTML(job.error_message)}</small>`;
    }
    const stats = job.stats || {};
    if (job.status === 'succeeded') {
        return `<strong>入库 ${fmtNumber(stats.inserted_posts)} 条</strong><small>重复 ${fmtNumber(stats.duplicate_posts)} · 拒绝 ${fmtNumber(stats.rejected_records)}</small>`;
    }
    return '<small>等待任务更新</small>';
}

async function loadCollection() {
    const jobs = await api('/api/v1/collection-jobs');
    document.getElementById('collection-rows').innerHTML = jobs.length ? jobs.map((job) => `
        <tr>
            <td>${fmtTime(job.created_at)}</td>
            <td>${escapeHTML(job.adapter === 'file' ? '文件导入' : job.adapter === 'rss' ? 'RSS' : '微博采集')}</td>
            <td>${escapeHTML((job.keywords || []).join('、'))}</td>
            <td>${statusBadge(job.status)}</td>
            <td>${escapeHTML(job.progress)}%</td>
            <td>${collectionDetail(job)}</td>
        </tr>`).join('') : '<tr><td colspan="6" class="empty-cell">还没有数据接入记录</td></tr>';
}

function analyticsMonitorId() {
    return document.getElementById('analytics-monitor')?.value || '';
}

function renderAnalyticsProvenance(data) {
    const host = document.getElementById('analytics-provenance');
    const provenance = data.provenance || {};
    const range = provenance.observed_from && provenance.observed_to
        ? `${fmtTime(provenance.observed_from)} — ${fmtTime(provenance.observed_to)}`
        : '暂无有效时间范围';
    const methods = (provenance.sentiment_methods || [])
        .map((item) => `${item.method} ${fmtNumber(item.count)} 条`)
        .join('、') || '未记录';
    host.className = `provenance-strip analytics-provenance ${provenance.data_mode === 'demo' ? 'demo' : ''}`;
    host.innerHTML = `
        <span><strong>数据性质：</strong>${escapeHTML(provenance.data_mode_label || '未标记')}</span>
        <span><strong>样本：</strong>${fmtNumber(provenance.sample_count)} / ${fmtNumber(provenance.total_available)} 条</span>
        <span><strong>观察窗口：</strong>${escapeHTML(range)}</span>
        <span><strong>情感方法：</strong>${escapeHTML(methods)}</span>
        <span><strong>边界：</strong>${escapeHTML(provenance.note || '')}</span>`;
}

function renderAnalyticsWordCloud(words) {
    const host = document.getElementById('analytics-word-cloud');
    if (!words.length) {
        host.innerHTML = empty('当前项目没有可统计的文本词频。');
        return;
    }
    const visible = words.slice(0, 10);
    const maximum = Math.max(...visible.map((item) => Number(item.count || 0)), 1);
    host.innerHTML = `<div class="term-rank-list">${visible.map((item, index) => {
        const width = Number(item.count || 0) / maximum * 100;
        return `<article class="term-rank-row${index === 0 ? ' leader' : ''}">
            <span class="term-rank-number">${String(index + 1).padStart(2, '0')}</span>
            <div class="term-rank-main">
                <div><strong>${escapeHTML(item.word)}</strong><span>${fmtNumber(item.count)} 次</span></div>
                <div class="term-track" role="img" aria-label="${escapeHTML(item.word)}出现 ${fmtNumber(item.count)} 次"><span style="width:${width.toFixed(1)}%"></span></div>
            </div>
        </article>`;
    }).join('')}</div>` + chartTable(
        ['排名', '词语', '出现次数'],
        words.map((item, index) => [index + 1, item.word, item.count]),
        '当前监测项目高频词完整数据',
    );
}

function renderAnalyticsTrend(data) {
    const host = document.getElementById('analytics-trend');
    if (!data.length) {
        host.innerHTML = empty('没有带有效时间的信号，无法绘制趋势。');
        return;
    }
    const width = 780;
    const height = 318;
    const left = 50;
    const right = 18;
    const top = 34;
    const bottom = 68;
    const plotWidth = width - left - right;
    const plotHeight = height - top - bottom;
    const maximum = Math.max(...data.map((item) => Number(item.total || 0)), 1);
    const step = plotWidth / data.length;
    const barWidth = Math.max(Math.min(step * .54, 74), 10);
    const baseline = top + plotHeight;
    let content = '';
    content += `<defs><pattern id="analytics-negative-pattern" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="7" height="7" fill="#d55e00"></rect><line x1="0" y1="0" x2="0" y2="7" stroke="rgba(255,255,255,.45)" stroke-width="2"></line></pattern></defs>`;
    for (let tick = 0; tick <= 4; tick += 1) {
        const value = Math.round(maximum * tick / 4);
        const tickY = baseline - plotHeight * tick / 4;
        content += `<line class="chart-gridline" x1="${left}" y1="${tickY}" x2="${width - right}" y2="${tickY}" />
            <text class="chart-label" x="${left - 8}" y="${tickY + 4}" text-anchor="end">${value}</text>`;
    }
    content += `<line class="chart-axis" x1="${left}" y1="${baseline}" x2="${width - right}" y2="${baseline}" />`;
    data.forEach((item, index) => {
        const x = left + index * step + (step - barWidth) / 2;
        const negative = Math.max(Number(item.negative || 0), 0);
        const nonnegative = Math.max(Number(item.total || 0) - negative, 0);
        const negativeHeight = negative / maximum * plotHeight;
        const nonnegativeHeight = nonnegative / maximum * plotHeight;
        const totalHeight = negativeHeight + nonnegativeHeight;
        const center = x + barWidth / 2;
        content += `<rect class="pulse-nonnegative" x="${x}" y="${baseline - totalHeight}" width="${barWidth}" height="${nonnegativeHeight}" rx="5"><title>${escapeHTML(vizTimeLabel(item.start, true))}：非负面 ${nonnegative} 条</title></rect>
            <rect class="pulse-negative" x="${x}" y="${baseline - negativeHeight}" width="${barWidth}" height="${negativeHeight}" rx="5"><title>${escapeHTML(vizTimeLabel(item.start, true))}：负面筛查 ${negative} 条</title></rect>
            <text class="pulse-total-label" x="${center}" y="${Math.max(baseline - totalHeight - 9, 14)}" text-anchor="middle">${item.total}</text>
            <text class="chart-label" x="${center}" y="${baseline + 23}" text-anchor="middle">${escapeHTML(vizTimeLabel(item.start, true))}</text>
            <text class="pulse-risk-label" x="${center}" y="${baseline + 43}" text-anchor="middle">风险词 ${item.total ? Math.round(item.risk / item.total * 100) : 0}%</text>`;
    });
    const peak = data.reduce((best, item) => Number(item.total || 0) > Number(best.total || 0) ? item : best, data[0]);
    const totalSignals = data.reduce((sum, item) => sum + Number(item.total || 0), 0);
    const totalNegative = data.reduce((sum, item) => sum + Number(item.negative || 0), 0);
    const negativeRatio = totalNegative / Math.max(totalSignals, 1) * 100;
    host.innerHTML = svgShell(
        width,
        height,
        '监测项目信号构成与负面强度',
        '每个时间窗使用共同零基线。橙色斜线段表示负面筛查，蓝色实色段表示其他信号，两段相加等于全部信号。',
        content,
    ) + `<div class="pulse-readout"><span><small>峰值窗口</small><strong>${escapeHTML(vizTimeLabel(peak.start, true))} · ${fmtNumber(peak.total)} 条</strong></span><span><small>当前范围负面筛查</small><strong>${negativeRatio.toFixed(1)}%</strong></span></div>
        <div class="chart-legend"><span><i class="legend-mark analytics-other"></i>非负面信号</span><span><i class="legend-mark analytics-hatched"></i>负面筛查</span><span>柱顶数字＝全部信号</span></div>`
        + chartTable(
            ['时间窗', '全部信号', '负面筛查', '非负面信号', '风险词信号'],
            data.map((item) => [vizTimeLabel(item.start, true), item.total, item.negative, Math.max(item.total - item.negative, 0), item.risk]),
            '监测项目信号构成底层数据',
        );
}

function renderAnalyticsEventRanking(events) {
    const host = document.getElementById('analytics-event-ranking');
    if (!events.length) {
        host.innerHTML = empty('当前样本尚未形成可比较的聚合事件。');
        return;
    }
    host.innerHTML = events.slice(0, 6).map((item, index) => `
        <article class="event-priority-card${Number(item.negative_ratio || 0) >= 80 ? ' high' : ''}">
            <div class="event-priority-head"><span>EVENT ${String(index + 1).padStart(2, '0')}</span><strong>热度 ${escapeHTML(item.heat_score || 0)}</strong></div>
            <button class="text-button" data-action="view-event" data-id="${escapeHTML(item.id)}">${escapeHTML(item.title)}</button>
            <div class="event-priority-stats"><span><strong>${fmtNumber(item.sample_count)}</strong>证据样本</span><span><strong>${fmtNumber(item.source_count)}</strong>来源类型</span><span><strong>${escapeHTML(trendNames[item.trend] || item.trend || '未知')}</strong>当前趋势</span></div>
            <div class="event-risk-row"><span>负面筛查</span><strong>${escapeHTML(item.negative_ratio)}%</strong></div>
            <div class="event-risk-track" role="img" aria-label="${escapeHTML(item.title)}负面筛查 ${escapeHTML(item.negative_ratio)}%"><span style="width:${Math.max(Math.min(Number(item.negative_ratio || 0), 100), 0)}%"></span></div>
        </article>`).join('');
}

function renderAnalyticsSources(sources) {
    const host = document.getElementById('analytics-sources');
    if (!sources.length) {
        host.innerHTML = empty('当前项目没有可统计的数据来源。');
        return;
    }
    const compositionLabel = sources.map((item) => `${item.label} ${item.share}%`).join('，');
    host.innerHTML = `<div class="source-composition" role="img" aria-label="来源构成：${escapeHTML(compositionLabel)}">${sources.map((item, index) => `<span class="source-tone-${index % 5}" style="width:${Math.max(Number(item.share || 0), .5)}%"><i>${Number(item.share || 0) >= 16 ? `${escapeHTML(item.share)}%` : ''}</i></span>`).join('')}</div>
        <div class="source-legend-list">${sources.map((item, index) => `
            <article class="source-legend-row">
                <i class="source-dot source-tone-${index % 5}"></i>
                <div><strong>${escapeHTML(item.label)}</strong><small>负面筛查 ${escapeHTML(item.negative_ratio)}% · 互动量 ${fmtNumber(item.engagement)}</small></div>
                <span><strong>${fmtNumber(item.count)}</strong><small>${escapeHTML(item.share)}%</small></span>
            </article>`).join('')}</div>` + chartTable(
        ['来源', '样本数', '样本占比', '负面筛查占比', '互动量'],
        sources.map((item) => [item.label, item.count, `${item.share}%`, `${item.negative_ratio}%`, item.engagement]),
        '当前监测项目来源结构完整数据',
    );
}

function renderAnalyticsReadiness(readiness) {
    const host = document.getElementById('analytics-readiness');
    const definitions = [
        ['lda', 'LDA 主题', `${fmtNumber(readiness.documents)} 篇有效文本`],
        ['arima', 'ARIMA 趋势', `${fmtNumber(readiness.observed_days)} 个观测日`],
        ['sir', 'SIR 情景', `${fmtNumber(readiness.observed_days)} 个观测日`],
    ];
    host.innerHTML = definitions.map(([key, label, current]) => {
        const item = readiness[key] || {};
        return `<article class="readiness-card ${item.available ? 'ready' : 'blocked'}">
            <div><strong>${escapeHTML(label)}</strong>${statusBadge(item.available ? 'succeeded' : 'paused')}</div>
            <p>${escapeHTML(current)} · 最低要求 ${escapeHTML(item.minimum)}</p>
            <small>${escapeHTML(item.note || '')}</small>
        </article>`;
    }).join('');
}

function analysisResultHTML(job) {
    if (!job) return empty('还没有运行当前项目的高级实验模型。');
    if (job.error_message) {
        return `<div class="notice error"><strong>${escapeHTML(job.error_code || 'analysis_failed')}</strong> · ${escapeHTML(job.error_message)}</div>`;
    }
    if (!job.result) {
        return `<div class="notice">${statusBadge(job.status)} 分析任务进度 ${escapeHTML(job.progress)}%，页面会自动刷新。</div>`;
    }
    const result = job.result;
    const summary = result.summary || {};
    const sections = [`<article><strong>摘要与词频</strong><p>样本 ${fmtNumber(summary.sample_count)} 条，负面筛查 ${escapeHTML(summary.negative_ratio || 0)}%。</p><small>${(summary.top_words || []).slice(0, 8).map((item) => escapeHTML(Array.isArray(item) ? item[0] : item.word)).join('、') || '暂无高频词'}</small></article>`];
    for (const [key, label] of [['lda', 'LDA 主题'], ['arima', 'ARIMA 趋势'], ['sir', 'SIR 情景']]) {
        const item = result[key];
        if (!item) continue;
        const note = item.available === false
            ? item.reason
            : key === 'lda'
                ? `${(item.topics || []).length} 个主题，轮廓系数 ${item.silhouette_score ?? '未提供'}`
                : key === 'arima'
                    ? `${item.observed_points || 0} 个观测点，预测 ${item.forecast?.length || 0} 期`
                    : `峰值日 ${item.peak_day ?? '未提供'}，拟合 NRMSE ${item.fit_nrmse ?? '未提供'}`;
        sections.push(`<article class="${item.available === false ? 'blocked' : ''}"><strong>${escapeHTML(label)}</strong><p>${escapeHTML(note || '分析已完成')}</p><small>${escapeHTML(item.claim_scope || '实验模型结果必须结合原始证据人工解释。')}</small></article>`);
    }
    return `<div class="analysis-result-head"><span>最近任务</span>${statusBadge(job.status)}</div><div class="analysis-result-grid">${sections.join('')}</div>`;
}

async function loadAnalytics() {
    const monitorId = analyticsMonitorId() || monitors[0]?.id || '';
    const select = document.getElementById('analytics-monitor');
    if (monitorId && select) select.value = monitorId;
    document.getElementById('analysis-monitor-id').value = monitorId;
    if (!monitorId) {
        document.getElementById('analytics-content').classList.add('hidden');
        const emptyHost = document.getElementById('analytics-empty');
        emptyHost.classList.remove('hidden');
        emptyHost.innerHTML = '<strong>还没有监测项目</strong><span>请先创建监测项目并接入数据，再进入分析实验室。</span>';
        return;
    }
    const data = await api(`/api/v2/analytics?monitor_id=${encodeURIComponent(monitorId)}`);
    renderAnalyticsProvenance(data);
    document.getElementById('analytics-signals').textContent = fmtNumber(data.summary.signals);
    document.getElementById('analytics-negative').textContent = `${Number(data.summary.negative_ratio || 0).toFixed(1)}%`;
    document.getElementById('analytics-negative-count').textContent = `${fmtNumber(data.summary.negative)} 条负面筛查`;
    document.getElementById('analytics-sources-count').textContent = fmtNumber((data.sources || []).length);
    document.getElementById('analytics-events-count').textContent = fmtNumber(data.summary.events);
    const emptyHost = document.getElementById('analytics-empty');
    const content = document.getElementById('analytics-content');
    const hasSignals = Number(data.summary.signals || 0) > 0;
    emptyHost.classList.toggle('hidden', hasSignals);
    content.classList.toggle('hidden', !hasSignals);
    if (!hasSignals) {
        emptyHost.innerHTML = '<strong>当前监测项目还没有匹配信号</strong><span>运行监测、导入带来源的数据，或切换到已有数据的监测项目。</span>';
        return;
    }
    renderAnalyticsWordCloud(data.top_words || []);
    renderAnalyticsTrend(data.timeline || []);
    renderAnalyticsEventRanking(data.events || []);
    renderAnalyticsSources(data.sources || []);
    renderAnalyticsReadiness(data.readiness || {});
    await loadAnalysis();
}

async function loadAnalysis() {
    const jobs = await api('/api/v1/analysis-jobs');
    const monitorId = analyticsMonitorId();
    const scopedJobs = jobs.filter((job) => (job.params || {}).monitor_id === monitorId);
    document.getElementById('analysis-result').innerHTML = analysisResultHTML(scopedJobs[0]);
    document.getElementById('analysis-rows').innerHTML = scopedJobs.length ? scopedJobs.map((job) => {
        const summary = job.result?.summary;
        const detail = job.error_message
            ? `<strong>${escapeHTML(job.error_code || 'error')}</strong><small>${escapeHTML(job.error_message)}</small>`
            : summary ? `负面筛查 ${escapeHTML(summary.negative_ratio)}%` : '等待任务更新';
        return `<tr>
            <td>${fmtTime(job.created_at)}</td>
            <td>${escapeHTML(monitorName((job.params || {}).monitor_id))}</td>
            <td>${escapeHTML(job.analysis_type)}</td>
            <td>${statusBadge(job.status)}</td>
            <td>${summary?.sample_count ?? '—'}</td>
            <td>${detail}</td>
        </tr>`;
    }).join('') : '<tr><td colspan="6" class="empty-cell">当前监测项目还没有实验分析任务</td></tr>';
}

async function loadReports() {
    const reports = await api('/api/v1/reports');
    document.getElementById('report-rows').innerHTML = reports.length ? reports.map((report) => {
        let action = report.error_message
            ? `<strong>${escapeHTML(report.error_code || 'error')}</strong><small>${escapeHTML(report.error_message)}</small>`
            : '<small>等待生成</small>';
        if (report.status === 'succeeded') {
            action = `<a class="button secondary" href="/api/v1/reports/${encodeURIComponent(report.id)}.${escapeHTML(report.format)}">下载</a>`;
        }
        return `<tr>
            <td>${fmtTime(report.created_at)}</td>
            <td>${escapeHTML(report.format.toUpperCase())}</td>
            <td>${escapeHTML(report.filters.topic || '全部')}</td>
            <td>${statusBadge(report.status)}</td>
            <td>${fmtNumber(report.sample_count)}</td>
            <td>${action}</td>
        </tr>`;
    }).join('') : '<tr><td colspan="6" class="empty-cell">还没有导出报告</td></tr>';
}

async function loadDeliveries() {
    const deliveries = await api('/api/v2/deliveries');
    document.getElementById('delivery-list').innerHTML = deliveries.length
        ? deliveries.map((item) => `<div class="compact-item">
            <div><h3>${escapeHTML(item.channel)} · ${escapeHTML(item.destination)}</h3><p>${fmtTime(item.created_at)}${item.error_message ? ` · ${escapeHTML(item.error_message)}` : ''}</p></div>
            ${statusBadge(item.status)}
        </div>`).join('')
        : empty('尚无 Webhook 通知记录。');
}

async function loadSettings() {
    const [settings, v2] = await Promise.all([
        api('/api/v1/settings/status'),
        api('/api/v2/settings')
    ]);
    let ready = {status: 'degraded', database: false, queue: settings.queue};
    try {
        const response = await fetch('/api/v1/health/ready');
        const payload = await response.json();
        ready = payload.data || ready;
    } catch (_) {
        // The visible degraded state is more useful than hiding the settings panel.
    }
    const badge = document.getElementById('ready-badge');
    badge.textContent = ready.status === 'ready' ? '运行就绪' : '配置待完善';
    badge.className = `status ${ready.status === 'ready' ? 'succeeded' : 'paused'}`;
    document.getElementById('settings-list').innerHTML = `
        <div><dt>管理员</dt><dd>${escapeHTML(settings.admin_username)}</dd></div>
        <div><dt>任务队列</dt><dd>${escapeHTML(settings.queue.mode)} · ${settings.queue.ready ? '可用' : '不可用'}</dd></div>
        <div><dt>数据库</dt><dd>${escapeHTML(settings.database_path)}</dd></div>
        <div><dt>监测调度</dt><dd>每 ${escapeHTML(v2.scheduler_interval_seconds)} 秒检查到期项目</dd></div>
        <div><dt>证据简报</dt><dd>${v2.llm.enabled ? `模型 ${escapeHTML(v2.llm.model)}` : '确定性本地模式'}</dd></div>
        <div><dt>微博适配器</dt><dd>${settings.collector.installed && settings.collector.command_configured ? '已配置' : '待配置'}</dd></div>
        <div><dt>使用边界</dt><dd>${settings.collector.license_accepted ? '已确认非商业用途' : '尚未确认非商业用途'}</dd></div>`;
    await loadDeliveries();
}

async function refreshCurrent() {
    const section = document.querySelector('.nav-link.active')?.dataset.section || 'overview';
    const loader = {
        overview: loadOverview,
        monitors: loadMonitors,
        signals: loadSignals,
        events: loadEvents,
        alerts: loadAlerts,
        analytics: loadAnalytics,
        data: loadCollection,
        reports: loadReports,
        settings: loadSettings
    }[section];
    if (loader) await loader();
}

document.querySelectorAll('.nav-link').forEach((button) => {
    button.addEventListener('click', () => activateSection(button.dataset.section));
});

document.querySelectorAll('[data-goto]').forEach((button) => {
    button.addEventListener('click', () => activateSection(button.dataset.goto));
});

document.querySelectorAll('[data-refresh]').forEach((button) => {
    button.addEventListener('click', () => {
        const loader = {
            overview: loadOverview,
            monitors: loadMonitors,
            analytics: loadAnalytics,
            data: loadCollection,
            reports: loadReports
        }[button.dataset.refresh];
        if (loader) loader().catch((error) => notify(error.message, true));
    });
});

document.getElementById('monitor-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const payload = {
        name: form.get('name'),
        description: form.get('description'),
        keywords: splitTerms(form.get('keywords')),
        required_terms: splitTerms(form.get('required_terms')),
        excluded_terms: splitTerms(form.get('excluded_terms')),
        risk_terms: splitTerms(form.get('risk_terms')),
        semantic_query: form.get('semantic_query'),
        feed_urls: String(form.get('feed_urls') || '').split('\n').map((value) => value.trim()).filter(Boolean),
        include_weibo: form.get('include_weibo') === 'on',
        interval_minutes: Number(form.get('interval_minutes')),
        negative_threshold: Number(form.get('negative_threshold')),
        spike_threshold: Number(form.get('spike_threshold')),
        webhook_url: form.get('webhook_url')
    };
    try {
        await api('/api/v2/monitors', {method: 'POST', body: JSON.stringify(payload)});
        notify('监测项目已创建');
        formElement.reset();
        formElement.elements.interval_minutes.value = '60';
        formElement.elements.negative_threshold.value = '50';
        formElement.elements.spike_threshold.value = '5';
        await loadMonitors();
        await loadOverview();
    } catch (error) {
        notify(error.message, true);
    }
});

document.getElementById('signal-filter').addEventListener('submit', (event) => {
    event.preventDefault();
    loadSignals().catch((error) => notify(error.message, true));
});
document.getElementById('event-monitor').addEventListener('change', () => {
    loadEvents().catch((error) => notify(error.message, true));
});
document.getElementById('alert-filter').addEventListener('submit', (event) => {
    event.preventDefault();
    loadAlerts().catch((error) => notify(error.message, true));
});
document.getElementById('analytics-filter').addEventListener('submit', (event) => {
    event.preventDefault();
    loadAnalytics().catch((error) => notify(error.message, true));
});
document.getElementById('analytics-monitor').addEventListener('change', () => {
    loadAnalytics().catch((error) => notify(error.message, true));
});

document.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-action]');
    if (!button) return;
    button.disabled = true;
    try {
        if (button.dataset.action === 'run-monitor') {
            await api(`/api/v2/monitors/${encodeURIComponent(button.dataset.id)}/run`, {method: 'POST'});
            notify('监测运行已提交');
            await Promise.all([loadMonitors(), loadOverview()]);
        } else if (button.dataset.action === 'monitor-status') {
            await api(`/api/v2/monitors/${encodeURIComponent(button.dataset.id)}/status`, {
                method: 'POST',
                body: JSON.stringify({status: button.dataset.status})
            });
            notify(button.dataset.status === 'active' ? '监测已启用' : '监测已暂停');
            await loadMonitors();
        } else if (button.dataset.action === 'open-monitor-signals') {
            activateSection('signals');
            document.querySelector('#signal-filter [name="monitor_id"]').value = button.dataset.id;
            await loadSignals();
        } else if (button.dataset.action === 'open-monitor-events') {
            activateSection('events');
            document.getElementById('event-monitor').value = button.dataset.id;
            await loadEvents();
        } else if (button.dataset.action === 'open-monitor-analytics') {
            activateSection('analytics');
            document.getElementById('analytics-monitor').value = button.dataset.id;
            document.getElementById('analysis-monitor-id').value = button.dataset.id;
            await loadAnalytics();
        } else if (button.dataset.action === 'view-event') {
            await openEvent(button.dataset.id);
        } else if (button.dataset.action === 'create-brief') {
            const brief = await api(`/api/v2/events/${encodeURIComponent(button.dataset.id)}/briefs`, {method: 'POST'});
            notify(brief.status === 'succeeded' ? '事件简报已生成' : '事件简报已提交生成');
            await openEvent(button.dataset.id);
        } else if (button.dataset.action === 'transition-alert') {
            const note = window.prompt('可选：记录本次处置说明', '') ?? '';
            await api(`/api/v2/alerts/${encodeURIComponent(button.dataset.id)}/transition`, {
                method: 'POST',
                body: JSON.stringify({status: button.dataset.status, note})
            });
            notify('预警状态已更新');
            await Promise.all([loadAlerts(), loadOverview()]);
        }
    } catch (error) {
        notify(error.message, true);
    } finally {
        button.disabled = false;
    }
});

document.querySelector('[data-close-dialog]').addEventListener('click', () => {
    document.getElementById('event-dialog').close();
});
document.getElementById('event-dialog').addEventListener('click', (event) => {
    if (event.target === event.currentTarget) event.currentTarget.close();
});

document.getElementById('collection-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
        await api('/api/v1/collection-jobs', {
            method: 'POST',
            body: JSON.stringify({
                keywords: form.get('keywords'),
                max_pages: Number(form.get('max_pages'))
            })
        });
        notify('微博采集任务已创建');
        formElement.reset();
        formElement.elements.max_pages.value = '3';
        await loadCollection();
    } catch (error) {
        notify(error.message, true);
    }
});

document.getElementById('import-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    try {
        await api('/api/v1/imports', {
            method: 'POST',
            body: new FormData(formElement)
        });
        notify('数据已提交导入');
        formElement.reset();
        await loadCollection();
    } catch (error) {
        notify(error.message, true);
    }
});

document.getElementById('analysis-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    try {
        const monitorId = analyticsMonitorId();
        if (!monitorId) throw new Error('请先选择监测项目');
        document.getElementById('analysis-monitor-id').value = monitorId;
        await api('/api/v1/analysis-jobs', {
            method: 'POST',
            body: JSON.stringify(Object.fromEntries(new FormData(formElement)))
        });
        notify('当前监测项目的实验分析任务已创建');
        await loadAnalysis();
    } catch (error) {
        notify(error.message, true);
    }
});

document.getElementById('report-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    try {
        await api('/api/v1/reports', {
            method: 'POST',
            body: JSON.stringify(Object.fromEntries(new FormData(formElement)))
        });
        notify('报告生成任务已创建');
        await loadReports();
    } catch (error) {
        notify(error.message, true);
    }
});

document.getElementById('password-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    try {
        await api('/api/v1/auth/password', {
            method: 'POST',
            body: JSON.stringify(Object.fromEntries(new FormData(formElement)))
        });
        window.location.href = '/login';
    } catch (error) {
        notify(error.message, true);
    }
});

document.getElementById('logout').addEventListener('click', async () => {
    try {
        await api('/api/v1/auth/logout', {method: 'POST'});
    } finally {
        window.location.href = '/login';
    }
});

(async () => {
    try {
        await loadSession();
        await loadMonitors();
        activateSection(location.hash.slice(1) || 'overview');
        await Promise.all([loadCollection(), loadReports()]);
        refreshTimer = window.setInterval(() => {
            if (!document.hidden) {
                refreshCurrent().catch((error) => notify(error.message, true));
            }
        }, 15000);
    } catch (error) {
        notify(error.message, true);
    }
})();
