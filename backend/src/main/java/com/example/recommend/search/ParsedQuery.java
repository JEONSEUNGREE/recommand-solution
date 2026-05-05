package com.example.recommend.search;

import java.util.Map;

/** LLM이 자연어를 파싱한 결과. filters는 nullable 값을 가진 맵. */
public record ParsedQuery(Map<String, Object> filters, String semanticQuery) {}
