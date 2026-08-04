'use strict';

let currentVisualStory = null;
let chartIdCounter = 0;

const vizClamp = (value, minimum, maximum) => Math.min(Math.max(value, minimum), maximum);
const vizTruncate = (value, length = 18) => {
    const text = String(value || '');
    return text.length > length ? `${text.slice(0, length)}…` : text;
};
const vizPercent = (value) => `${Number(value || 0).toFixed(1).replace('.0', '')}%`;
const vizTimeLabel = (value, withDate = false) => {
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return '时间未知';
    return parsed.toLocaleString('zh-CN', withDate
        ? {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false}
        : {hour: '2-digit', minute: '2-digit', hour12: false});
};

function chartTable(headers, rows, caption) {
    const head = headers.map((item) => `<th scope="col">${escapeHTML(item)}</th>`).join('');
    const body = rows.map((row) => `<tr>${row.map((item) => `<td>${escapeHTML(item)}</td>`).join('')}</tr>`).join('');
    return `<details class="chart-table">
        <summary>查看图表数据表</summary>
        <div class="table-wrap"><table><caption class="hidden">${escapeHTML(caption)}</caption><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>
    </details>`;
}

function svgShell(width, height, title, description, content) {
    chartIdCounter += 1;
    const titleId = `chart-title-${chartIdCounter}`;
    const descId = `${titleId}-description`;
    return `<svg viewBox="0 0 ${width} ${height}" role="img" tabindex="0" aria-labelledby="${titleId} ${descId}">
        <title id="${titleId}">${escapeHTML(title)}</title>
        <desc id="${descId}">${escapeHTML(description)}</desc>
        ${content}
    </svg>`;
}

function renderTimeline(story) {
    const host = document.getElementById('timeline-chart');
    const data = story.timeline || [];
    if (!data.length) {
        host.innerHTML = empty('没有带有效时间的信号，无法绘制演化图。');
        return;
    }
    const width = 920;
    const height = 390;
    const left = 54;
    const right = 24;
    const top = 28;
    const countBottom = 236;
    const riskTop = 286;
    const riskBottom = 348;
    const plotWidth = width - left - right;
    const countHeight = countBottom - top;
    const maxCount = Math.max(...data.map((item) => item.total), 1);
    const step = plotWidth / data.length;
    const barWidth = Math.max(Math.min(step * .62, 34), 4);
    let content = '';
    for (let tick = 0; tick <= 4; tick += 1) {
        const value = Math.round(maxCount * tick / 4);
        const y = countBottom - countHeight * tick / 4;
        content += `<line class="chart-gridline" x1="${left}" y1="${y}" x2="${width - right}" y2="${y}" />
            <text class="chart-label" x="${left - 9}" y="${y + 4}" text-anchor="end">${value}</text>`;
    }
    content += `<text class="chart-title-label" x="${left}" y="16">信号数（条）</text>`;
    data.forEach((item, index) => {
        const x = left + index * step + (step - barWidth) / 2;
        const totalHeight = item.total / maxCount * countHeight;
        const negativeHeight = item.negative / maxCount * countHeight;
        content += `<rect class="chart-total" x="${x}" y="${countBottom - totalHeight}" width="${barWidth}" height="${totalHeight}" rx="2">
                <title>${escapeHTML(vizTimeLabel(item.start, true))}：${item.total} 条信号</title>
            </rect>
            <rect class="chart-negative" x="${x}" y="${countBottom - negativeHeight}" width="${barWidth}" height="${negativeHeight}" rx="2">
                <title>负面筛查 ${item.negative} 条</title>
            </rect>`;
        if (index % Math.max(Math.ceil(data.length / 7), 1) === 0 || index === data.length - 1) {
            content += `<text class="chart-label" x="${x + barWidth / 2}" y="${countBottom + 20}" text-anchor="middle">${escapeHTML(vizTimeLabel(item.start, true))}</text>`;
        }
    });
    content += `<line class="chart-axis" x1="${left}" y1="${countBottom}" x2="${width - right}" y2="${countBottom}" />
        <text class="chart-title-label" x="${left}" y="${riskTop - 12}">风险词信号占比（0–100%）</text>
        <line class="chart-gridline" x1="${left}" y1="${riskTop}" x2="${width - right}" y2="${riskTop}" />
        <line class="chart-axis" x1="${left}" y1="${riskBottom}" x2="${width - right}" y2="${riskBottom}" />
        <text class="chart-label" x="${left - 9}" y="${riskTop + 4}" text-anchor="end">100%</text>
        <text class="chart-label" x="${left - 9}" y="${riskBottom + 4}" text-anchor="end">0%</text>`;
    const riskPoints = data.map((item, index) => {
        const ratio = item.total ? item.risk / item.total * 100 : 0;
        return {
            x: left + index * step + step / 2,
            y: riskBottom - ratio / 100 * (riskBottom - riskTop),
            ratio,
            label: vizTimeLabel(item.start, true),
        };
    });
    const path = riskPoints.map((point, index) => `${index ? 'L' : 'M'} ${point.x.toFixed(1)} ${point.y.toFixed(1)}`).join(' ');
    content += `<path class="chart-risk-line" d="${path}" />`;
    riskPoints.forEach((point) => {
        content += `<circle class="chart-risk-point" cx="${point.x}" cy="${point.y}" r="3.5"><title>${escapeHTML(point.label)}：${point.ratio.toFixed(1)}%</title></circle>`;
    });
    host.innerHTML = svgShell(
        width,
        height,
        '声量与风险演化',
        `共 ${story.summary.signals} 条信号。上方面板以共同零基线展示总量和负面筛查量，下方面板展示风险词信号占比。`,
        content,
    ) + `<div class="chart-legend"><span><i class="legend-mark"></i>全部信号</span><span><i class="legend-mark negative"></i>负面筛查</span><span><i class="legend-mark risk"></i>风险词占比</span></div>`
        + chartTable(
            ['时间窗', '全部信号', '负面筛查', '风险词信号', '互动量'],
            data.map((item) => [vizTimeLabel(item.start, true), item.total, item.negative, item.risk, item.engagement]),
            '声量与风险演化底层数据',
        );
}

function renderSources(story) {
    const host = document.getElementById('source-chart');
    const sources = story.sources || [];
    if (!sources.length) {
        host.innerHTML = empty('没有可统计的数据来源。');
        return;
    }
    const maxCount = Math.max(...sources.map((item) => item.count), 1);
    host.innerHTML = `<div class="source-bars">${sources.map((source) => `
        <article class="source-row">
            <header><span>${escapeHTML(source.label)}</span><strong>${fmtNumber(source.count)} 条 · ${vizPercent(source.share)}</strong></header>
            <div class="source-track" role="img" aria-label="${escapeHTML(source.label)} ${source.count} 条，占 ${source.share}%">
                <div class="source-fill" style="width:${source.count / maxCount * 100}%"></div>
            </div>
            <div class="source-note">负面筛查 ${vizPercent(source.negative_ratio)} · 互动量 ${fmtNumber(source.engagement)}</div>
        </article>`).join('')}</div>` + chartTable(
            ['来源', '样本数', '样本占比', '负面筛查占比', '互动量'],
            sources.map((item) => [item.label, item.count, vizPercent(item.share), vizPercent(item.negative_ratio), item.engagement]),
            '来源构成底层数据',
        );
}

function renderRiskMap(story) {
    const host = document.getElementById('risk-map');
    const events = (story.events || []).slice(0, 8);
    if (!events.length) {
        host.innerHTML = empty('尚未形成可比较的聚合事件。');
        return;
    }
    const width = 760;
    const height = 390;
    const left = 58;
    const right = 30;
    const top = 28;
    const bottom = 52;
    const plotWidth = width - left - right;
    const plotHeight = height - top - bottom;
    const maxSample = Math.max(...events.map((item) => item.sample_count), 1);
    let content = '';
    for (let tick = 0; tick <= 4; tick += 1) {
        const value = tick * 25;
        const x = left + plotWidth * tick / 4;
        const y = top + plotHeight - plotHeight * tick / 4;
        content += `<line class="chart-gridline" x1="${x}" y1="${top}" x2="${x}" y2="${top + plotHeight}" />
            <line class="chart-gridline" x1="${left}" y1="${y}" x2="${left + plotWidth}" y2="${y}" />
            <text class="chart-label" x="${x}" y="${height - 23}" text-anchor="middle">${value}%</text>
            <text class="chart-label" x="${left - 9}" y="${y + 4}" text-anchor="end">${value}</text>`;
    }
    content += `<line class="chart-axis" x1="${left}" y1="${top + plotHeight}" x2="${left + plotWidth}" y2="${top + plotHeight}" />
        <line class="chart-axis" x1="${left}" y1="${top}" x2="${left}" y2="${top + plotHeight}" />
        <text class="chart-title-label" x="${left + plotWidth / 2}" y="${height - 3}" text-anchor="middle">负面筛查占比</text>
        <text class="chart-title-label" x="15" y="${top + plotHeight / 2}" transform="rotate(-90 15 ${top + plotHeight / 2})" text-anchor="middle">热度评分（描述性）</text>`;
    const points = events.map((item) => {
        const x = left + vizClamp(item.negative_ratio, 0, 100) / 100 * plotWidth;
        const y = top + plotHeight - vizClamp(item.heat_score, 0, 100) / 100 * plotHeight;
        const area = 130 + item.sample_count / maxSample * 470;
        const radius = Math.sqrt(area / Math.PI);
        const tone = item.negative_ratio >= 70 || item.heat_score >= 75 ? '#d55e00' : item.heat_score >= 45 ? '#0072b2' : '#009e73';
        const trend = {rising: '↑', emerging: '◆', declining: '↓', stable: '—'}[item.trend] || '·';
        const side = x > left + plotWidth * .56 ? 'left' : 'right';
        return {item, x, y, radius, tone, trend, side, labelY: y};
    });
    ['left', 'right'].forEach((side) => {
        const group = points.filter((point) => point.side === side).sort((a, b) => a.y - b.y);
        const gap = 30;
        const minimum = top + (side === 'left' ? 42 : 12);
        const maximum = top + plotHeight - 12;
        group.forEach((point, index) => {
            point.labelY = Math.max(point.y, index ? group[index - 1].labelY + gap : minimum);
        });
        if (group.length && group[group.length - 1].labelY > maximum) {
            group[group.length - 1].labelY = maximum;
            for (let index = group.length - 2; index >= 0; index -= 1) {
                group[index].labelY = Math.min(group[index].labelY, group[index + 1].labelY - gap);
            }
        }
    });
    const rows = [];
    points.forEach(({item, x, y, radius, tone, trend, side, labelY}) => {
        const direction = side === 'left' ? -1 : 1;
        const labelX = x + direction * (radius + 10);
        const anchor = side === 'left' ? 'end' : 'start';
        const lineStartX = x + direction * radius;
        const lineEndX = labelX - direction * 4;
        content += `<g class="graph-node" role="button" tabindex="0" data-action="view-event" data-id="${escapeHTML(item.id)}" aria-label="打开事件 ${escapeHTML(item.title)}">
            <circle cx="${x}" cy="${y}" r="${radius.toFixed(1)}" fill="${tone}" fill-opacity=".16" stroke="${tone}" stroke-width="2"><title>${escapeHTML(item.title)}：热度 ${item.heat_score}，负面 ${item.negative_ratio}%，样本 ${item.sample_count}</title></circle>
            <text x="${x}" y="${y + 4}" text-anchor="middle" fill="${tone}" font-size="12" font-weight="900">${trend}</text>
            <path class="risk-label-line" d="M ${lineStartX} ${y} L ${lineEndX} ${labelY}" />
            <text class="chart-value" x="${labelX}" y="${labelY - 3}" text-anchor="${anchor}">${escapeHTML(vizTruncate(item.title, 15))}</text>
            <text class="chart-label" x="${labelX}" y="${labelY + 12}" text-anchor="${anchor}">n=${item.sample_count} · ${item.source_count}来源</text>
        </g>`;
        rows.push([item.title, item.heat_score, vizPercent(item.negative_ratio), item.sample_count, item.source_count, item.trend]);
    });
    host.innerHTML = svgShell(
        width,
        height,
        '事件风险矩阵',
        '每个圆代表一个聚合事件。横轴为负面筛查占比，纵轴为描述性热度，圆面积按样本量缩放，箭头或符号表示趋势。',
        content,
    ) + `<div class="chart-legend"><span>圆面积＝样本量</span><span>引导线＝错位标注</span><span>↑ 上升</span><span>◆ 新出现</span><span>↓ 下降</span><span>— 平稳</span></div>`
        + chartTable(['事件', '热度', '负面筛查', '样本', '来源', '趋势'], rows, '事件风险矩阵底层数据');
}

function renderEventLanes(story) {
    const host = document.getElementById('event-lanes');
    const events = (story.events || []).slice(0, 8);
    if (!events.length) {
        host.innerHTML = empty('尚未形成事件时间带。');
        return;
    }
    const maxBin = Math.max(...events.flatMap((item) => item.series), 1);
    host.innerHTML = `<div class="event-lane-list">${events.map((item) => `
        <article class="event-lane">
            <div class="event-lane-label"><button class="text-button" data-action="view-event" data-id="${escapeHTML(item.id)}"><strong>${escapeHTML(item.title)}</strong></button><small>${escapeHTML(trendNames[item.trend] || item.trend)} · ${item.source_count} 类来源</small></div>
            <div class="event-lane-track" role="img" aria-label="${escapeHTML(item.title)}在各时间窗的信号数：${escapeHTML(item.series.join('、'))}">
                ${item.series.map((value) => `<span class="event-lane-bar" style="height:${Math.max(value / maxBin * 100, value ? 8 : 2)}%" title="${value} 条"></span>`).join('')}
            </div>
            <div class="event-lane-stat"><strong>${item.sample_count}</strong>条证据</div>
        </article>`).join('')}</div>` + chartTable(
            ['事件', '首次出现', '最后出现', '样本', '负面筛查', '热度'],
            events.map((item) => [item.title, fmtTime(item.first_seen), fmtTime(item.last_seen), item.sample_count, vizPercent(item.negative_ratio), item.heat_score]),
            '事件演化时间带底层数据',
        );
}

function competitionEventDetailHTML(eventId) {
    const item = currentVisualStory?.events?.find((event) => String(event.id) === String(eventId));
    if (!item) return '';
    const timeline = currentVisualStory.timeline || [];
    const maximum = Math.max(...item.series, 1);
    const bars = item.series.map((value, index) => {
        const label = timeline[index] ? vizTimeLabel(timeline[index].start, true) : `时间窗 ${index + 1}`;
        const height = Math.max(value / maximum * 100, value ? 10 : 2);
        return `<div class="event-mini-bin" title="${escapeHTML(label)}：${value} 条">
            <span class="event-mini-bar" style="height:${height}%"></span>
            <small>${escapeHTML(label)}</small>
            <strong>${value}</strong>
        </div>`;
    }).join('');
    const sources = (item.sources || []).map((source) =>
        `<span>${escapeHTML(source.label)} <strong>${fmtNumber(source.count)}</strong></span>`
    ).join('');
    const risks = (item.risk_terms || []).map((risk) =>
        `<span>${escapeHTML(risk.term)} <strong>${fmtNumber(risk.count)}</strong></span>`
    ).join('');
    return `<section class="event-viz-detail" aria-labelledby="event-viz-heading">
        <div class="event-viz-heading">
            <div><p class="eyebrow">EVENT EVOLUTION</p><h3 id="event-viz-heading">事件演化与来源</h3></div>
            <span class="data-mode-badge ${escapeHTML(currentVisualStory.provenance?.data_mode || 'observed')}">${escapeHTML(currentVisualStory.provenance?.data_mode_label || '观测范围数据')}</span>
        </div>
        <div class="event-viz-metrics">
            <span><strong>${fmtNumber(item.sample_count)}</strong>条证据</span>
            <span><strong>${vizPercent(item.negative_ratio)}</strong>负面筛查</span>
            <span><strong>${fmtNumber(item.engagement)}</strong>互动量</span>
            <span><strong>${escapeHTML(trendNames[item.trend] || item.trend)}</strong>当前趋势</span>
        </div>
        <div class="event-mini-chart" role="img" aria-label="${escapeHTML(item.title)}各时间窗信号数：${escapeHTML(item.series.join('、'))}">${bars}</div>
        <div class="event-detail-groups">
            <div><strong>来源构成</strong><div class="event-chip-list">${sources || '<span>未记录</span>'}</div></div>
            <div><strong>风险词命中</strong><div class="event-chip-list risk">${risks || '<span>未命中</span>'}</div></div>
        </div>
        <p class="event-viz-note">图中数值仅统计当前监测项目的入库样本；热度、负面与风险词都是筛查指标，请继续核验下方原始证据。</p>
    </section>`;
}

function renderEvidenceGraph(story) {
    const host = document.getElementById('evidence-network');
    const graph = story.graph || {nodes: [], edges: []};
    const sources = graph.nodes.filter((item) => item.type === 'source');
    const events = graph.nodes.filter((item) => item.type === 'event');
    const root = graph.nodes.find((item) => item.type === 'monitor');
    if (!root || !sources.length || !events.length) {
        host.innerHTML = empty('来源和事件不足，暂时无法形成证据关系网。');
        return;
    }
    const width = 1040;
    const height = Math.max(350, Math.max(sources.length, events.length) * 78 + 80);
    const positions = new Map();
    positions.set(root.id, {x: 95, y: height / 2});
    sources.forEach((item, index) => positions.set(item.id, {x: 390, y: (index + 1) * height / (sources.length + 1)}));
    events.forEach((item, index) => positions.set(item.id, {x: 820, y: (index + 1) * height / (events.length + 1)}));
    const maxNode = Math.max(...graph.nodes.map((item) => item.value || 0), 1);
    let content = `<text class="chart-title-label" x="45" y="22">监测范围</text>
        <text class="chart-title-label" x="342" y="22">数据来源</text>
        <text class="chart-title-label" x="770" y="22">聚合事件</text>`;
    graph.edges.forEach((edge) => {
        const source = positions.get(edge.source);
        const target = positions.get(edge.target);
        if (!source || !target) return;
        const middle = (source.x + target.x) / 2;
        content += `<path class="graph-edge" d="M ${source.x} ${source.y} C ${middle} ${source.y}, ${middle} ${target.y}, ${target.x} ${target.y}" stroke-width="${Math.min(1 + Math.sqrt(edge.value || 1), 8)}"><title>${edge.value} 条证据</title></path>`;
    });
    content += `<g class="graph-node"><rect class="graph-monitor" x="28" y="${height / 2 - 34}" width="134" height="68" rx="14" />
        <text class="graph-label inverse" x="95" y="${height / 2 - 3}" text-anchor="middle">${escapeHTML(vizTruncate(root.label, 10))}</text>
        <text x="95" y="${height / 2 + 16}" text-anchor="middle" fill="#a7d9d6" font-size="10">${root.value} 条信号</text></g>`;
    sources.forEach((item) => {
        const point = positions.get(item.id);
        const area = 150 + item.value / maxNode * 520;
        const radius = Math.sqrt(area / Math.PI);
        content += `<g class="graph-node"><circle class="graph-source" cx="${point.x}" cy="${point.y}" r="${radius}" stroke-width="2" />
            <text class="graph-label" x="${point.x}" y="${point.y + 4}" text-anchor="middle">${escapeHTML(vizTruncate(item.label, 8))}</text>
            <text class="chart-label" x="${point.x}" y="${point.y + radius + 14}" text-anchor="middle">${item.value} 条</text></g>`;
    });
    events.forEach((item) => {
        const point = positions.get(item.id);
        const area = 180 + item.value / maxNode * 620;
        const radius = Math.sqrt(area / Math.PI);
        content += `<g class="graph-node" role="button" tabindex="0" data-action="view-event" data-id="${escapeHTML(item.event_id)}" aria-label="打开事件 ${escapeHTML(item.label)}"><circle class="graph-event" cx="${point.x}" cy="${point.y}" r="${radius}" stroke-width="2" />
            <text class="graph-label" x="${point.x + radius + 8}" y="${point.y - 2}">${escapeHTML(vizTruncate(item.label, 18))}</text>
            <text class="chart-label" x="${point.x + radius + 8}" y="${point.y + 14}">${item.value} 条 · 热度 ${item.heat_score}</text></g>`;
    });
    document.getElementById('network-meaning').textContent = graph.meaning;
    host.innerHTML = svgShell(
        width,
        height,
        '来源—事件证据关系网',
        `${graph.meaning} 节点面积按纳入的信号数量缩放，连线粗细按证据数量缩放。`,
        content,
    ) + `<div class="chart-legend"><span>节点面积＝证据数量</span><span>连线粗细＝归属数量</span><span>点击事件节点可核验原文</span></div>`
        + chartTable(
            ['来源', '事件', '证据数量'],
            graph.edges.filter((edge) => edge.source.startsWith('source:')).map((edge) => {
                const source = graph.nodes.find((item) => item.id === edge.source);
                const event = graph.nodes.find((item) => item.id === edge.target);
                return [source?.label || edge.source, event?.label || edge.target, edge.value];
            }),
            '来源—事件证据关系底层数据',
        );
}

function renderStoryText(story) {
    const provenance = story.provenance;
    const badge = document.getElementById('story-data-badge');
    badge.textContent = provenance.data_mode_label;
    badge.className = `data-mode-badge ${provenance.data_mode}`;
    document.getElementById('story-window').textContent = provenance.observed_from
        ? `观察窗口 ${fmtTime(provenance.observed_from)} — ${fmtTime(provenance.observed_to)} · ${provenance.bin_hours || '—'} 小时聚合`
        : '当前样本没有有效时间字段';
    const methodText = (provenance.sentiment_methods || [])
        .map((item) => `${item.method} ${item.count}条`).join('；') || '未记录';
    const strip = document.getElementById('story-provenance');
    strip.className = `provenance-strip ${provenance.data_mode}`;
    strip.innerHTML = `<span><strong>数据性质：</strong>${escapeHTML(provenance.data_mode_label)}</span>
        <span><strong>样本：</strong>${fmtNumber(provenance.sample_count)} / ${fmtNumber(provenance.total_available)} 条${provenance.truncated ? '（视图已截断）' : ''}</span>
        <span><strong>有效时间：</strong>${fmtNumber(provenance.dated_count)} 条</span>
        <span><strong>缺失时间：</strong>${fmtNumber(provenance.missing_time_count)} 条</span>
        <span title="${escapeHTML(methodText)}"><strong>情感方法：</strong>${escapeHTML(methodText)}</span>
        <span><strong>边界：</strong>${escapeHTML(provenance.note)}</span>`;
    document.getElementById('metric-signals').textContent = fmtNumber(story.summary.signals);
    document.getElementById('metric-events').textContent = fmtNumber(story.summary.events);
    document.getElementById('metric-risk-signals').textContent = fmtNumber(story.summary.risk_signals);
    document.getElementById('metric-risk-ratio').textContent = `占当前样本 ${vizPercent(story.summary.risk_ratio)}`;
    document.getElementById('metric-alerts').textContent = fmtNumber(story.summary.open_alerts);
    document.getElementById('metric-high-alerts').textContent = `${fmtNumber(story.summary.high_alerts)} 条高风险`;
    document.getElementById('metric-sample-note').textContent = `负面筛查 ${vizPercent(story.summary.negative_ratio)}`;
    document.getElementById('story-insights').innerHTML = (story.insights || [])
        .map((item) => `<li>${escapeHTML(item)}</li>`).join('');
    const definitions = story.definitions || {};
    document.getElementById('story-definitions').innerHTML = `
        <strong>指标口径</strong><br>
        热度：${escapeHTML(definitions.heat || '—')}<br>
        关系网：${escapeHTML(definitions.network || '—')}`;
}

function showStoryEmpty(message) {
    const host = document.getElementById('story-empty');
    host.classList.remove('hidden');
    document.getElementById('story-dashboard').classList.add('hidden');
    host.innerHTML = `<strong>还没有可讲述的事件脉络</strong><p>${escapeHTML(message)}</p><button class="primary" data-action="seed-demo">载入固定演示案例</button>`;
}

async function loadCompetitionStory() {
    const select = document.getElementById('story-monitor');
    const monitorId = select.value || monitors[0]?.id;
    if (!monitorId) {
        showStoryEmpty('可以导入带来源和时间的数据，或载入明确标记的合成案例体验完整可视化。');
        return;
    }
    select.value = monitorId;
    const story = await api(`/api/v2/visual-story?monitor_id=${encodeURIComponent(monitorId)}`);
    currentVisualStory = story;
    if (!story.summary.signals) {
        renderStoryText(story);
        showStoryEmpty('该监测项目还没有匹配信号。请先运行监测，或载入离线演示案例。');
        return;
    }
    document.getElementById('story-empty').classList.add('hidden');
    document.getElementById('story-dashboard').classList.remove('hidden');
    renderStoryText(story);
    renderTimeline(story);
    renderSources(story);
    renderRiskMap(story);
    renderEventLanes(story);
    renderEvidenceGraph(story);
}

async function seedCompetitionDemo() {
    const accepted = window.confirm(
        '将向本地数据库加入 54 条明确标记的合成校园案例，用于离线演示。不会删除或冒充真实数据。是否继续？'
    );
    if (!accepted) return;
    const result = await api('/api/v2/demo/seed', {method: 'POST'});
    notify(`演示案例已就绪：${result.records} 条记录，新增 ${result.inserted} 条`);
    await loadMonitors();
    document.getElementById('story-monitor').value = result.monitor.id;
    await loadOverview();
}

document.getElementById('story-monitor').addEventListener('change', () => {
    loadCompetitionStory().catch((error) => notify(error.message, true));
});

document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-action="seed-demo"]');
    if (!button) return;
    event.stopImmediatePropagation();
    button.disabled = true;
    seedCompetitionDemo()
        .catch((error) => notify(error.message, true))
        .finally(() => { button.disabled = false; });
});

document.addEventListener('keydown', (event) => {
    const target = event.target.closest?.('[data-action="view-event"][role="button"]');
    if (target && ['Enter', ' '].includes(event.key)) {
        event.preventDefault();
        target.dispatchEvent(new MouseEvent('click', {bubbles: true}));
    }
});
