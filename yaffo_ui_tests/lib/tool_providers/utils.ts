const MAX_TOOL_RESULT_CHARS = 15000;
export const truncateToolResultIfNeeded = (result: string): string => {
    if (result.length <= MAX_TOOL_RESULT_CHARS) {
        return result;
    }
    console.warn(`   ⚠️  Result truncated: ${result.length} → ${MAX_TOOL_RESULT_CHARS} chars`);
    const totalLines = result.split('\n').length;
    let truncated = result.slice(0, MAX_TOOL_RESULT_CHARS);

    const lastNewlineIndex = truncated.lastIndexOf('\n');
    if (lastNewlineIndex > 0) {
        truncated = truncated.slice(0, lastNewlineIndex);
    }

    const returnedLines = truncated.split('\n').length;
    const truncatedMsg = `\n\n[TRUNCATED: Showing ${returnedLines} of ${totalLines} lines (${truncated.length} of ${result.length} chars)]`;
    return truncated + truncatedMsg;
};