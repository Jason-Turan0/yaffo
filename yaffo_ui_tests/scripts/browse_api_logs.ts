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

interface Message {
    role: string;
    content: ContentBlock[] | string;
}

interface ApiLogEntry {
    timestamp: string;
    durationMs: number;
    request: {
        model: string;
        messages: Message[];
        system?: ContentBlock[];
    };
    response?: {
        content: ContentBlock[];
        stop_reason: string;
    };
    success: boolean;
    cacheUsage?: {
        inputTokens: number;
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

const getAllRuns = (): RunInfo[] => {
    const runs: RunInfo[] = [];
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

    // Sort oldest to newest
    return runs.sort((a, b) => {
        const dateA = a.timestamp.replace('T', ' ');
        const dateB = b.timestamp.replace('T', ' ');
        return dateA.localeCompare(dateB);
    });
};

const formatContentBlock = (block: ContentBlock, indent: string = ''): string => {
    switch (block.type) {
        case 'text':
            const textPreview = (block.text || '').slice(0, 100).replace(/\n/g, '\\n');
            return `${indent}[text] ${textPreview}${(block.text || '').length > 100 ? '...' : ''}`;
        case 'tool_use':
            const inputPreview = JSON.stringify(block.input || {}).slice(0, 60);
            return `${indent}[tool_use] ${block.name}(${inputPreview}${inputPreview.length >= 60 ? '...' : ''})`;
        case 'tool_result':
            const contentStr = typeof block.content === 'string'
                ? block.content.slice(0, 80)
                : JSON.stringify(block.content).slice(0, 80);
            return `${indent}[tool_result] id:${block.tool_use_id?.slice(0, 12)}... → ${contentStr.replace(/\n/g, '\\n')}${contentStr.length >= 80 ? '...' : ''}`;
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


const printFullMessage = (msg: Message) => {
    const content = msg.content;
    if (typeof content === 'string') {
        return `${msg.role}: ${content}...`;
    }

    const blocks = content.map(b => {
        const indent = '      ';
        let outputText = '';
        outputText += `${indent}type: ${b.type}\n`
        try {
            if (b.type === 'tool_use') {
                outputText += `name=${b.name}\n`
                outputText += JSON.stringify(b.input, null, 2);
            } else {
                for (const contentElement of b.content as ContentBlock[]) {
                    outputText += contentElement.text + "\n";
                }

            }

        } catch (e) {
            outputText = b.text;
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

const summarizeApiCall = (filePath: string, index: number): string => {
    const data: ApiLogEntry = JSON.parse(fs.readFileSync(filePath, 'utf8'));
    const msgCount = data.request.messages?.length || 0;
    const stopReason = data.response?.stop_reason || 'N/A';
    const cost = data.costEstimate?.call?.totalCost?.toFixed(4) || 'N/A';
    const duration = (data.durationMs / 1000).toFixed(1);

    let responsePreview = '';
    if (data.response?.content) {
        const firstContent = data.response.content[0];
        if (firstContent?.type === 'text') {
            responsePreview = (firstContent.text || '').slice(0, 50).replace(/\n/g, '\\n');
        } else if (firstContent?.type === 'tool_use') {
            responsePreview = `tool:${firstContent.name}`;
        }
    }

    return `[${index.toString().padStart(2)}] msgs:${msgCount.toString().padStart(2)} | ${stopReason.padEnd(8)} | $${cost} | ${duration}s | ${responsePreview}`;
};


const printApiCallDetail = async (filePath: string): Promise<void> => {
    const data: ApiLogEntry = JSON.parse(fs.readFileSync(filePath, 'utf8'));

    console.log('\n' + '='.repeat(80));
    console.log(`Timestamp: ${data.timestamp}`);
    console.log(`Duration: ${(data.durationMs / 1000).toFixed(2)}s`);
    console.log(`Model: ${data.request.model}`);
    console.log(`Success: ${data.success}`);
    console.log(`Stop Reason: ${data.response?.stop_reason || 'N/A'}`);

    if (data.cacheUsage) {
        console.log(`Tokens: ${data.cacheUsage.inputTokens} in / ${data.cacheUsage.outputTokens} out`);
    }
    if (data.costEstimate?.call) {
        console.log(`Cost: $${data.costEstimate.call.totalCost.toFixed(4)}`);
    }

    console.log('\n--- SYSTEM PROMPT ---');
    if (data.request.system) {
        for (const block of data.request.system) {
            if (block.type === 'text') {
                console.log((block.text || '').slice(0, 500) + '...');
            }
        }
    }

    console.log('\n--- MESSAGES ---');
    const messages = data.request.messages || [];
    for (let i = 0; i < messages.length; i++) {
        console.log(formatMessage(messages[i], i));
    }

    console.log('\n--- RESPONSE ---');
    if (data.response?.content) {
        for (const block of data.response.content) {
            console.log(formatContentBlock(block, '  '));
            if (block.type === 'text' && block.text) {
                console.log('\n  Full text:');
                console.log('  ' + block.text.slice(0, 2000).replace(/\n/g, '\n  '));
                if (block.text.length > 2000) {
                    console.log(`  ... (${block.text.length - 2000} more chars)`);
                }
            }
        }
    }
    console.log('='.repeat(80));

    while (true) {
        const input = await prompt('\nEnter index to view message details, "b" to go back: ');

        if (input.toLowerCase() === 'back' || input.toLowerCase() === 'b') {
            return;
        }

        const index = parseInt(input);
        if (!isNaN(index) && index >= 0 && index < data.request.messages.length) {
            console.log(printFullMessage(data.request.messages[index]));
        } else {
            console.log('Invalid input. Enter a number or "b" to go back.');
        }
    }
};

const browseRun = async (runInfo: RunInfo): Promise<void> => {
    const files = listApiCalls(runInfo.fullPath);

    console.log(`\n📁 ${runInfo.feature}/${runInfo.timestamp} (${files.length} API calls)\n`);

    for (let i = 0; i < files.length; i++) {
        const filePath = path.join(runInfo.fullPath, files[i]);
        console.log(summarizeApiCall(filePath, i));
    }

    while (true) {
        const input = await prompt('\nEnter index to view details, "b" to go back: ');

        if (input.toLowerCase() === 'b' || input.toLowerCase() === 'back') {
            return;
        }

        const index = parseInt(input);
        if (!isNaN(index) && index >= 0 && index < files.length) {
            const filePath = path.join(runInfo.fullPath, files[index]);
            await printApiCallDetail(filePath);
        } else {
            console.log('Invalid input. Enter a number or "b" to go back.');
        }
    }
};

const main = async (): Promise<void> => {
    console.log('🔍 API Log Browser\n');

    while (true) {
        const runs = getAllRuns();

        console.log('\nAvailable runs (newest first):\n');
        for (let i = 0; i < runs.length; i++) {
            const run = runs[i];
            console.log(`[${i.toString().padStart(2)}] ${run.feature.padEnd(20)} ${run.timestamp} (${run.fileCount} calls)`);
        }

        const input = await prompt('\nEnter index, filter text, "latest", or "q" to quit: ');

        if (input.toLowerCase() === 'q' || input.toLowerCase() === 'quit') {
            console.log('Goodbye!');
            rl.close();
            return;
        }

        if (input.toLowerCase() === 'latest') {
            if (runs.length > 0) {
                await browseRun(runs[0]);
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
                console.log(`[${i.toString().padStart(2)}] ${run.feature.padEnd(20)} ${run.timestamp} (${run.fileCount} calls)`);
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