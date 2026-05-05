package com.example.recommend.search;

import com.example.recommend.llm.LlmClient;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Component;

import java.util.HashMap;
import java.util.Iterator;
import java.util.Map;

/** 자연어 → ParsedQuery (filters + semantic_query). LLM 1차 호출. */
@Component
public class QueryParser {

    private static final String SYSTEM = """
            당신은 의류 쇼핑몰 검색 쿼리를 구조화 JSON으로 변환합니다.

            가능한 카테고리: 상의, 하의, 아우터, 원피스, 신발, 가방, 액세서리
            가능한 성별: 여성, 남성, 공용
            가능한 시즌: 봄, 여름, 가을, 겨울, 사계절
            가능한 스타일: 캐주얼, 미니멀, 스트릿, 클래식, 빈티지, 페미닌, 스포티, 데일리, 오피스, 러블리, 모던, 보헤미안

            반드시 다음 JSON 스키마로만 응답:
            {
              "filters": {
                "category": string|null,
                "gender": string|null,
                "season": string|null,
                "style": string|null,
                "color": string|null,
                "price_min": number|null,
                "price_max": number|null,
                "in_stock_only": boolean
              },
              "semantic_query": "벡터 검색용 핵심 의미 (한국어 자연어 그대로)"
            }

            명시되지 않은 필드는 null. 재고 언급 없으면 in_stock_only=true.
            """;

    private final LlmClient llm;
    private final ObjectMapper mapper;

    public QueryParser(LlmClient llm, ObjectMapper mapper) {
        this.llm = llm;
        this.mapper = mapper;
    }

    public ParsedQuery parse(String userText) {
        String raw = llm.chat(SYSTEM, userText, 512);
        String json = LlmClient.stripFence(raw);
        try {
            JsonNode root = mapper.readTree(json);
            Map<String, Object> filters = new HashMap<>();
            JsonNode f = root.get("filters");
            Iterator<Map.Entry<String, JsonNode>> it = f.fields();
            while (it.hasNext()) {
                Map.Entry<String, JsonNode> e = it.next();
                JsonNode v = e.getValue();
                if (v == null || v.isNull()) continue;
                if (v.isBoolean())       filters.put(e.getKey(), v.asBoolean());
                else if (v.isNumber())   filters.put(e.getKey(), v.numberValue());
                else if (v.isTextual())  filters.put(e.getKey(), v.asText());
            }
            String sem = root.get("semantic_query").asText();
            return new ParsedQuery(filters, sem);
        } catch (Exception e) {
            throw new RuntimeException("Failed to parse LLM JSON: " + json, e);
        }
    }
}
