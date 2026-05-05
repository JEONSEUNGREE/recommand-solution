package com.example.recommend.search;

import com.example.recommend.llm.LlmClient;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;

/** 후보 리스트를 LLM에 보내 상위 5개 + 추천 이유 생성. */
@Component
public class Reranker {

    private static final String SYSTEM = """
            당신은 패션 큐레이터입니다. 사용자 요구와 후보 상품 목록을 보고:
            1) 가장 적합한 5개를 골라 순위 매기고
            2) 각각 한 줄로 추천 이유를 작성하세요.

            JSON으로만 응답:
            {"recommendations": [{"product_id": int, "reason": "..."}]}
            """;

    public record Recommendation(long productId, String reason) {}

    private final LlmClient llm;
    private final ObjectMapper mapper;

    public Reranker(LlmClient llm, ObjectMapper mapper) {
        this.llm = llm;
        this.mapper = mapper;
    }

    public List<Recommendation> rerank(String userQuery, List<Product> candidates) {
        if (candidates.isEmpty()) return List.of();

        ObjectNode payload = mapper.createObjectNode();
        payload.put("user_query", userQuery);
        ArrayNode arr = payload.putArray("candidates");
        for (Product p : candidates) {
            ObjectNode c = arr.addObject();
            c.put("product_id", p.id());
            c.put("name", p.name());
            c.put("category", p.category());
            c.put("subcategory", p.subcategory());
            c.put("gender", p.gender());
            c.put("season", p.season());
            c.put("style", p.style());
            c.put("color", p.color());
            c.put("price", p.salePrice() != null ? p.salePrice() : p.price());
        }

        String raw = llm.chat(SYSTEM, payload.toString(), 1024);
        String json = LlmClient.stripFence(raw);
        try {
            JsonNode root = mapper.readTree(json);
            List<Recommendation> out = new ArrayList<>();
            for (JsonNode r : root.get("recommendations")) {
                out.add(new Recommendation(r.get("product_id").asLong(), r.get("reason").asText()));
            }
            return out;
        } catch (Exception e) {
            throw new RuntimeException("Failed to parse rerank JSON: " + json, e);
        }
    }
}
