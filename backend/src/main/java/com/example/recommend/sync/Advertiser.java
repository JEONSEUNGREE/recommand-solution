package com.example.recommend.sync;

import java.time.OffsetDateTime;

public record Advertiser(
        Long id,
        String name,
        String hostType,
        String shopUrl,
        String shopKey,
        String licenseKey,
        String cafe24MallId,
        String cafe24AccessToken,
        String notes,
        // selector override (1:1 — deprecated, 사용은 advertiser_selectors 테이블)
        String selectorName,
        String selectorDetail,
        String selectorPrice,
        String imageAttrs,
        // anchor (HTML 주석/문자열 사이만 잘라내기 — selector보다 우선)
        String detailAnchorStart,
        String detailAnchorEnd,
        OffsetDateTime createdAt,
        OffsetDateTime updatedAt
) {}
