import Foundation

// analyze — judgment step (Anthropic API)
struct Step01_analyze_In: Codable, Sendable {
    var text: String
}

struct Step01_analyze_Out: Codable, Sendable {
    var result: SentimentResult
}

func step_analyze(_ input: Step01_analyze_In) async throws -> Step01_analyze_Out {
    // 1. Build prompt from input.
    let encoder = JSONEncoder()
    let inData = try encoder.encode(input)
    let inJSON = String(data: inData, encoding: .utf8) ?? "{}"
    let prompt = "Process this input and return JSON matching the output schema.\n\nInput:\n\(inJSON)"
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
        throw AnthropicError(message: "analyze: response is not valid UTF-8")
    }
    let out = try JSONDecoder().decode(Step01_analyze_Out.self, from: rawData)
    // 6. Validate contract.
    try out.result.validate()
    return out
}
