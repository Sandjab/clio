import Foundation

// summarize — judgment step (Anthropic API)
struct Step01_summarize_In: Codable, Sendable {
    var text: String
}

struct Step01_summarize_Out: Codable, Sendable {
    var summary: String
}

func step_summarize(_ input: Step01_summarize_In) async throws -> Step01_summarize_Out {
    // 1. Build prompt from input.
    let encoder = JSONEncoder()
    let inData = try encoder.encode(input)
    let inJSON = String(data: inData, encoding: .utf8) ?? "{}"
    let prompt = "Process this input and return JSON matching the output schema.\n\nInput:\n\(inJSON)"
    // 2. Cache lookup.
    let cacheDir = Cache.cacheDirFromEnv()
    let cacheKey = Cache.key(step: "summarize", model: "claude-haiku-4-5-20251001", prompt: prompt, schema: "")
    if let hit = Cache.lookup(cacheDir: cacheDir, stepName: "summarize", key: cacheKey, ttlSeconds: 86400),
       let hitData = hit.data(using: .utf8),
       let cached = try? JSONDecoder().decode(Step01_summarize_Out.self, from: hitData) {
        return cached
    }
    // 3. JSON-only system prompt (matches python/go targets).
    let system = "You are a strict JSON-only API. Output exactly one JSON document matching the requested schema, with no prose, no markdown code fences, no commentary, and no leading or trailing whitespace beyond the JSON itself."
    // 4. Call Anthropic.
    let raw = try await Anthropic.complete(
        model: "claude-haiku-4-5-20251001",
        system: system,
        prompt: prompt,
        maxTokens: 8192
    )
    // 5. Decode response into typed Out struct.
    guard let rawData = raw.data(using: .utf8) else {
        throw AnthropicError(message: "summarize: response is not valid UTF-8")
    }
    let out = try JSONDecoder().decode(Step01_summarize_Out.self, from: rawData)
    // 7. Store in cache.
    if let storeData = try? JSONEncoder().encode(out),
       let storeStr = String(data: storeData, encoding: .utf8) {
        Cache.store(cacheDir: cacheDir, stepName: "summarize", key: cacheKey, model: "claude-haiku-4-5-20251001", response: storeStr)
    }
    return out
}
