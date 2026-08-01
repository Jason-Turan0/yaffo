import * as fs from 'fs';
import * as path from 'path';
import * as readline from 'readline';

const API_LOGS_DIR = path.join(process.cwd(), 'reports', 'api_logs');

interface ContentBlock {
    type: string;
    text?: string;
    name?: string;
    input?: Record<string, unknown>;
    tool_use_id?: string;
    content?: string | ContentBlock[];
}

interface RequestBody {
    model: string;
    messages: Message[];
    system?: ContentBlock[];
}

interface Message {
    role: string;
    content: ContentBlock[] | string;
}

interface ApiLogEntry {
    timestamp: string;
    durationMs: number;
    request: {
        model: string;
        url?: string;
        body?: RequestBody;
        messages?: Message[];
        system?: ContentBlock[];
    };
    response?: {
        content: ContentBlock[];
        stop_reason: string;
    };
    success: boolean;
    cacheUsage?: {
        /** Uncached input only; totalInputTokens is the whole prompt. */
        inputTokens: number;
        totalInputTokens?: number;
        cacheReadInputTokens?: number;
        outputTokens: number;
    };
    costEstimate?: {
        call: {
            totalCost: number;
        };
    };
}

interface RunInfo {
    feature: string;
    timestamp: string;
    fullPath: string;
    fileCount: number;
}

const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout
});

const prompt = (question: string): Promise<string> => {
    return new Promise((resolve) => {
        rl.question(question, resolve);
    });
};

const getMessages = (entry: ApiLogEntry): Message[] => {
    return entry.request.body?.messages ?? entry.request.messages ?? [];
};

const getSystem = (entry: ApiLogEntry): ContentBlock[] => {
    return entry.request.body?.system ?? entry.request.system ?? [];
};

const getModel = (entry: ApiLogEntry): string => {
    return entry.request.body?.model ?? entry.request.model ?? 'unknown';
};

const getAllRuns = (): RunInfo[] => {
    const runs: RunInfo[] = [];
    if (!fs.existsSync(API_LOGS_DIR)) return runs;

    const features = fs.readdirSync(API_LOGS_DIR).filter(f => {
        const fullPath = path.join(API_LOGS_DIR, f);
        return fs.statSync(fullPath).isDirectory() && !f.startsWith('.');
    });

    for (const feature of features) {
        const featurePath = path.join(API_LOGS_DIR, feature);
        const timestamps = fs.readdirSync(featurePath).filter(f => {
            const fullPath = path.join(featurePath, f);
            return fs.statSync(fullPath).isDirectory() && !f.startsWith('.');
        });

        for (const timestamp of timestamps) {
            const fullPath = path.join(featurePath, timestamp);
            const files = fs.readdirSync(fullPath).filter(f => f.endsWith('.json'));
            runs.push({
                feature,
                timestamp,
                fullPath,
                fileCount: files.length
            });
        }
    }

    return runs.sort((a, b) => {
        const dateA = a.timestamp.replace('T', ' ');
        const dateB = b.timestamp.replace('T', ' ');
        return dateA.localeCompare(dateB);
    });
};

const formatContentBlock = (block: ContentBlock, indent: string = ''): string => {
    switch (block.type) {
        case 'text': {
            const textPreview = (block.text || '').slice(0, 100).replace(/\n/g, '\\n');
            return `${indent}[text] ${textPreview}${(block.text || '').length > 100 ? '...' : ''}`;
        }
        case 'tool_use': {
            const inputPreview = JSON.stringify(block.input || {}).slice(0, 60);
            return `${indent}[tool_use] ${block.name}(${inputPreview}${inputPreview.length >= 60 ? '...' : ''})`;
        }
        case 'tool_result': {
            const contentStr = typeof block.content === 'string'
                ? block.content.slice(0, 80)
                : JSON.stringify(block.content || '').slice(0, 80);
            return `${indent}[tool_result] id:${block.tool_use_id?.slice(0, 12)}... → ${contentStr.replace(/\n/g, '\\n')}${contentStr.length >= 80 ? '...' : ''}`;
        }
        default:
            return `${indent}[${block.type}]`;
    }
};

const formatMessage = (msg: Message, index: number): string => {
    const content = msg.content;
    if (typeof content === 'string') {
        return `  [${index}] ${msg.role}: ${content.slice(0, 100)}...`;
    }

    const blocks = content.map(b => formatContentBlock(b, '      ')).join('\n');
    return `  [${index}] ${msg.role}:\n${blocks}`;
};

const tryPrettyPrintJson = (text: string): string => {
    try {
        const parsed = JSON.parse(text);
        return JSON.stringify(parsed, null, 2);
    } catch {
        return text;
    }
};

const printFullMessage = (msg: Message): string => {
    const content = msg.content;
    if (typeof content === 'string') {
        return `${msg.role}: ${tryPrettyPrintJson(content)}`;
    }

    const blocks = content.map(b => {
        const indent = '      ';
        let outputText = '';
        outputText += `${indent}type: ${b.type}\n`;
        if (b.type === 'tool_use') {
            outputText += `${indent}name: ${b.name}\n`;
            outputText += JSON.stringify(b.input, null, 2);
        } else if (b.type === 'tool_result') {
            outputText += `${indent}tool_use_id: ${b.tool_use_id}\n`;
            if (typeof b.content === 'string') {
                outputText += tryPrettyPrintJson(b.content);
            } else if (Array.isArray(b.content)) {
                for (const el of b.content) {
                    outputText += (el.text || '') + '\n';
                }
            }
        } else if (b.type === 'text') {
            outputText += tryPrettyPrintJson(b.text || '');
        }
        return `${indent}${outputText}`;
    }).join('\n');
    return `   ${msg.role}:\n${blocks}`;
};

const listApiCalls = (runPath: string): string[] => {
    return fs.readdirSync(runPath)
        .filter(f => f.endsWith('.json'))
        .sort((a, b) => {
            const numA = parseInt(a.split('_')[0]);
            const numB = parseInt(b.split('_')[0]);
            return numA - numB;
        });
};

const loadLogEntry = (filePath: string): ApiLogEntry => {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
};

const getResponseToolNames = (entry: ApiLogEntry): string[] => {
    if (!entry.response?.content) return [];
    return entry.response.content
        .filter(b => b.type === 'tool_use' && b.name)
        .map(b => b.name!);
};

const getResponseTextBlocks = (entry: ApiLogEntry): string[] => {
    if (!entry.response?.content) return [];
    return entry.response.content
        .filter(b => b.type === 'text' && b.text)
        .map(b => b.text!);
};

const hasJsonResponse = (entry: ApiLogEntry): boolean => {
    for (const text of getResponseTextBlocks(entry)) {
        try {
            JSON.parse(text.trim());
            return true;
        } catch {
            // not json
        }
    }
    return false;
};

const entryContainsText = (entry: ApiLogEntry, searchText: string): boolean => {
    const lower = searchText.toLowerCase();

    for (const text of getResponseTextBlocks(entry)) {
        if (text.toLowerCase().includes(lower)) return true;
    }

    for (const toolName of getResponseToolNames(entry)) {
        if (toolName.toLowerCase().includes(lower)) return true;
    }

    if (entry.response?.content) {
        for (const block of entry.response.content) {
            if (block.type === 'tool_use' && block.input) {
                if (JSON.stringify(block.input).toLowerCase().includes(lower)) return true;
            }
        }
    }

    const messages = getMessages(entry);
    for (const msg of messages) {
        if (typeof msg.content === 'string') {
            if (msg.content.toLowerCase().includes(lower)) return true;
        } else {
            for (const block of msg.content) {
                if (block.text?.toLowerCase().includes(lower)) return true;
                if (block.type === 'tool_result' && typeof block.content === 'string') {
                    if (block.content.toLowerCase().includes(lower)) return true;
                }
                if (block.type === 'tool_use' && block.input) {
                    if (JSON.stringify(block.input).toLowerCase().includes(lower)) return true;
                }
            }
        }
    }

    return false;
};

const summarizeApiCall = (filePath: string, index: number): string => {
    const data = loadLogEntry(filePath);
    const msgCount = getMessages(data).length;
    const stopReason = data.response?.stop_reason || 'N/A';
    const cost = data.costEstimate?.call?.totalCost?.toFixed(4) || 'N/A';
    const duration = (data.durationMs / 1000).toFixed(1);

    let responsePreview = '';
    if (data.response?.content) {
        const firstContent = data.response.content[0];
        if (firstContent?.type === 'text') {
            responsePreview = (firstContent.text || '').slice(0, 50).replace(/\n/g, '\\n');
        } else if (firstContent?.type === 'tool_use') {
            const toolNames = getResponseToolNames(data);
            responsePreview = `tools: ${toolNames.join(', ')}`;
        }
    }

    const jsonTag = hasJsonResponse(data) ? ' [JSON]' : '';
    return `[${index.toString().padStart(2)}] msgs:${msgCount.toString().padStart(2)} | ${stopReason.padEnd(10)} | $${cost.padEnd(7)} | ${duration.padEnd(5)}s | ${responsePreview}${jsonTag}`;
};

type DetailNavigation = 'next' | 'prev' | 'back';

interface DetailContext {
    filePath: string;
    displayIndex: number;
    totalInList: number;
}

const printApiCallDetail = async (ctx: DetailContext): Promise<DetailNavigation> => {
    const data = loadLogEntry(ctx.filePath);
    const messages = getMessages(data);

    console.log('\n' + '='.repeat(80));
    console.log(`[${ctx.displayIndex + 1}/${ctx.totalInList}] ${path.basename(ctx.filePath)}`);
    console.log(`Timestamp: ${data.timestamp}`);
    console.log(`Duration: ${(data.durationMs / 1000).toFixed(2)}s`);
    console.log(`Model: ${getModel(data)}`);
    console.log(`Success: ${data.success}`);
    console.log(`Stop Reason: ${data.response?.stop_reason || 'N/A'}`);

    if (data.cacheUsage) {
        // inputTokens is the uncached remainder, so show the whole prompt too —
        // otherwise a heavily cached call looks like it sent almost nothing.
        const {inputTokens, totalInputTokens, cacheReadInputTokens, outputTokens} = data.cacheUsage;
        const prompt = totalInputTokens ?? inputTokens;
        const cached = cacheReadInputTokens ? ` (${cacheReadInputTokens} cached)` : "";
        console.log(`Tokens: ${prompt} in${cached} / ${outputTokens} out`);
    }
    if (data.costEstimate?.call) {
        console.log(`Cost: $${data.costEstimate.call.totalCost.toFixed(4)}`);
    }

    console.log('\n--- SYSTEM PROMPT ---');
    const system = getSystem(data);
    if (system.length > 0) {
        for (const block of system) {
            if (block.type === 'text') {
                console.log((block.text || '').slice(0, 500) + '...');
            }
        }
    }

    console.log('\n--- MESSAGES ---');
    for (let i = 0; i < messages.length; i++) {
        console.log(formatMessage(messages[i], i));
    }

    console.log('\n--- RESPONSE ---');
    if (data.response?.content) {
        for (const block of data.response.content) {
            console.log(formatContentBlock(block, '  '));
            if (block.type === 'text' && block.text) {
                const formatted = tryPrettyPrintJson(block.text);
                const isJson = formatted !== block.text;
                console.log(`\n  Full text${isJson ? ' (JSON pretty-printed)' : ''}:`);
                const displayText = formatted.slice(0, 3000);
                console.log('  ' + displayText.replace(/\n/g, '\n  '));
                if (formatted.length > 3000) {
                    console.log(`  ... (${formatted.length - 3000} more chars)`);
                }
            }
        }
    }
    console.log('='.repeat(80));

    const hasPrev = ctx.displayIndex > 0;
    const hasNext = ctx.displayIndex < ctx.totalInList - 1;
    const navHints: string[] = [];
    if (hasPrev) navHints.push('"p" prev');
    if (hasNext) navHints.push('"n" next');

    while (true) {
        const input = await prompt(`\nMsg index, "r" response, ${navHints.join(', ')}${navHints.length > 0 ? ', ' : ''}"b" back: `);
        const cmd = input.trim().toLowerCase();

        if (cmd === 'back' || cmd === 'b') {
            return 'back';
        }

        if ((cmd === 'n' || cmd === 'next') && hasNext) {
            return 'next';
        }

        if ((cmd === 'p' || cmd === 'prev') && hasPrev) {
            return 'prev';
        }

        if ((cmd === 'n' || cmd === 'next') && !hasNext) {
            console.log('Already at the last call.');
            continue;
        }

        if ((cmd === 'p' || cmd === 'prev') && !hasPrev) {
            console.log('Already at the first call.');
            continue;
        }

        if (cmd === 'r') {
            if (data.response?.content) {
                for (const block of data.response.content) {
                    if (block.type === 'text' && block.text) {
                        console.log('\n--- FULL RESPONSE ---');
                        console.log(tryPrettyPrintJson(block.text));
                        console.log('--- END ---');
                    } else if (block.type === 'tool_use') {
                        console.log(`\n--- TOOL CALL: ${block.name} ---`);
                        console.log(JSON.stringify(block.input, null, 2));
                        console.log('--- END ---');
                    }
                }
            }
            continue;
        }

        const index = parseInt(input);
        if (!isNaN(index) && index >= 0 && index < messages.length) {
            console.log(printFullMessage(messages[index]));
        } else {
            console.log('Invalid input. Enter a number, "r", "n"/"p" to navigate, or "b" to go back.');
        }
    }
};

interface FilterState {
    toolName?: string;
    stopReason?: string;
    jsonOnly: boolean;
    searchText?: string;
}

const applyFilter = (files: string[], runPath: string, filter: FilterState): { file: string; originalIndex: number }[] => {
    const results: { file: string; originalIndex: number }[] = [];

    for (let i = 0; i < files.length; i++) {
        const filePath = path.join(runPath, files[i]);
        const entry = loadLogEntry(filePath);

        if (filter.toolName) {
            const tools = getResponseToolNames(entry);
            if (!tools.some(t => t.toLowerCase().includes(filter.toolName!.toLowerCase()))) {
                continue;
            }
        }

        if (filter.stopReason) {
            const sr = entry.response?.stop_reason || '';
            if (!sr.toLowerCase().includes(filter.stopReason.toLowerCase())) {
                continue;
            }
        }

        if (filter.jsonOnly && !hasJsonResponse(entry)) {
            continue;
        }

        if (filter.searchText && !entryContainsText(entry, filter.searchText)) {
            continue;
        }

        results.push({file: files[i], originalIndex: i});
    }

    return results;
};

const getRunToolSummary = (files: string[], runPath: string): Map<string, number> => {
    const counts = new Map<string, number>();
    for (const file of files) {
        const entry = loadLogEntry(path.join(runPath, file));
        for (const name of getResponseToolNames(entry)) {
            counts.set(name, (counts.get(name) || 0) + 1);
        }
    }
    return counts;
};

const getRunStopReasonSummary = (files: string[], runPath: string): Map<string, number> => {
    const counts = new Map<string, number>();
    for (const file of files) {
        const entry = loadLogEntry(path.join(runPath, file));
        const sr = entry.response?.stop_reason || 'N/A';
        counts.set(sr, (counts.get(sr) || 0) + 1);
    }
    return counts;
};

const printFilterHelp = (): void => {
    console.log('\nFilter commands:');
    console.log('  tool <name>     Filter by tool name (partial match)');
    console.log('  stop <reason>   Filter by stop reason (partial match)');
    console.log('  json            Show only calls with JSON text responses');
    console.log('  s <text>        Search for text across all messages/responses');
    console.log('  tools           List all tool names used in this run');
    console.log('  stops           List all stop reasons in this run');
    console.log('  all             Clear all filters');
    console.log('  <number>        View API call details');
    console.log('  b               Go back to run list');
};

const browseRun = async (runInfo: RunInfo): Promise<void> => {
    const files = listApiCalls(runInfo.fullPath);
    const filter: FilterState = {jsonOnly: false};

    const displayCalls = (filtered: { file: string; originalIndex: number }[]) => {
        const activeFilters: string[] = [];
        if (filter.toolName) activeFilters.push(`tool=${filter.toolName}`);
        if (filter.stopReason) activeFilters.push(`stop=${filter.stopReason}`);
        if (filter.jsonOnly) activeFilters.push('json-only');
        if (filter.searchText) activeFilters.push(`search="${filter.searchText}"`);

        const filterLabel = activeFilters.length > 0 ? ` [filters: ${activeFilters.join(', ')}]` : '';
        console.log(`\n📁 ${runInfo.feature}/${runInfo.timestamp} (${filtered.length}/${files.length} calls)${filterLabel}\n`);

        for (const item of filtered) {
            const filePath = path.join(runInfo.fullPath, item.file);
            console.log(summarizeApiCall(filePath, item.originalIndex));
        }
    };

    let filtered = applyFilter(files, runInfo.fullPath, filter);
    displayCalls(filtered);

    while (true) {
        const input = await prompt('\nEnter index, filter command, or "?" for help: ');
        const trimmed = input.trim();

        if (trimmed.toLowerCase() === 'b' || trimmed.toLowerCase() === 'back') {
            return;
        }

        if (trimmed === '?') {
            printFilterHelp();
            continue;
        }

        if (trimmed.toLowerCase() === 'all') {
            filter.toolName = undefined;
            filter.stopReason = undefined;
            filter.jsonOnly = false;
            filter.searchText = undefined;
            filtered = applyFilter(files, runInfo.fullPath, filter);
            displayCalls(filtered);
            continue;
        }

        if (trimmed.toLowerCase() === 'json') {
            filter.jsonOnly = !filter.jsonOnly;
            console.log(filter.jsonOnly ? '  JSON filter: ON' : '  JSON filter: OFF');
            filtered = applyFilter(files, runInfo.fullPath, filter);
            displayCalls(filtered);
            continue;
        }

        if (trimmed.toLowerCase().startsWith('tool ')) {
            const toolArg = trimmed.slice(5).trim();
            if (toolArg) {
                filter.toolName = toolArg;
                console.log(`  Tool filter: "${toolArg}"`);
            } else {
                filter.toolName = undefined;
                console.log('  Tool filter: cleared');
            }
            filtered = applyFilter(files, runInfo.fullPath, filter);
            displayCalls(filtered);
            continue;
        }

        if (trimmed.toLowerCase() === 'tools') {
            const summary = getRunToolSummary(files, runInfo.fullPath);
            console.log('\nTool usage in this run:');
            const sorted = Array.from(summary.entries()).sort((a, b) => b[1] - a[1]);
            for (const [name, count] of sorted) {
                console.log(`  ${name.padEnd(30)} ${count}x`);
            }
            continue;
        }

        if (trimmed.toLowerCase().startsWith('stop ')) {
            const stopArg = trimmed.slice(5).trim();
            if (stopArg) {
                filter.stopReason = stopArg;
                console.log(`  Stop reason filter: "${stopArg}"`);
            } else {
                filter.stopReason = undefined;
                console.log('  Stop reason filter: cleared');
            }
            filtered = applyFilter(files, runInfo.fullPath, filter);
            displayCalls(filtered);
            continue;
        }

        if (trimmed.toLowerCase() === 'stops') {
            const summary = getRunStopReasonSummary(files, runInfo.fullPath);
            console.log('\nStop reasons in this run:');
            for (const [reason, count] of summary.entries()) {
                console.log(`  ${reason.padEnd(20)} ${count}x`);
            }
            continue;
        }

        if (trimmed.toLowerCase().startsWith('s ')) {
            const searchArg = trimmed.slice(2).trim();
            if (searchArg) {
                filter.searchText = searchArg;
                console.log(`  Search filter: "${searchArg}"`);
            } else {
                filter.searchText = undefined;
                console.log('  Search filter: cleared');
            }
            filtered = applyFilter(files, runInfo.fullPath, filter);
            displayCalls(filtered);
            continue;
        }

        const index = parseInt(trimmed);
        if (!isNaN(index)) {
            let filteredIdx = filtered.findIndex(item => item.originalIndex === index);
            if (filteredIdx === -1 && index >= 0 && index < files.length) {
                filteredIdx = filtered.findIndex(item => item.originalIndex >= index);
                if (filteredIdx === -1) filteredIdx = filtered.length - 1;
            }

            if (filteredIdx >= 0 && filteredIdx < filtered.length) {
                let cursor = filteredIdx;
                while (cursor >= 0 && cursor < filtered.length) {
                    const item = filtered[cursor];
                    const filePath = path.join(runInfo.fullPath, item.file);
                    const nav = await printApiCallDetail({
                        filePath,
                        displayIndex: cursor,
                        totalInList: filtered.length,
                    });
                    if (nav === 'next') {
                        cursor++;
                    } else if (nav === 'prev') {
                        cursor--;
                    } else {
                        break;
                    }
                }
                displayCalls(filtered);
            } else {
                console.log(`Invalid index. Range: 0-${files.length - 1}`);
            }
            continue;
        }

        console.log('Unknown command. Enter "?" for help.');
    }
};

const main = async (): Promise<void> => {
    console.log('🔍 API Log Browser\n');

    while (true) {
        const runs = getAllRuns();

        console.log('\nAvailable runs (oldest first):\n');
        for (let i = 0; i < runs.length; i++) {
            const run = runs[i];
            console.log(`[${i.toString().padStart(2)}] ${run.feature.padEnd(25)} ${run.timestamp} (${run.fileCount} calls)`);
        }

        const input = await prompt('\nEnter index, filter text, "latest", or "q" to quit: ');

        if (input.toLowerCase() === 'q' || input.toLowerCase() === 'quit') {
            console.log('Goodbye!');
            rl.close();
            return;
        }

        if (input.toLowerCase() === 'latest') {
            if (runs.length > 0) {
                await browseRun(runs[runs.length - 1]);
            }
            continue;
        }

        const index = parseInt(input);
        if (!isNaN(index) && index >= 0 && index < runs.length) {
            await browseRun(runs[index]);
            continue;
        }

        const filtered = runs.filter(r =>
            r.feature.toLowerCase().includes(input.toLowerCase()) ||
            r.timestamp.toLowerCase().includes(input.toLowerCase())
        );

        if (filtered.length === 1) {
            await browseRun(filtered[0]);
        } else if (filtered.length > 1) {
            console.log(`\nFiltered runs matching "${input}":\n`);
            for (let i = 0; i < filtered.length; i++) {
                const run = filtered[i];
                console.log(`[${i.toString().padStart(2)}] ${run.feature.padEnd(25)} ${run.timestamp} (${run.fileCount} calls)`);
            }

            const subInput = await prompt('\nEnter index: ');
            const subIndex = parseInt(subInput);
            if (!isNaN(subIndex) && subIndex >= 0 && subIndex < filtered.length) {
                await browseRun(filtered[subIndex]);
            }
        } else {
            console.log('No matches found.');
        }
    }
};

main().catch(console.error);
