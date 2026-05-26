package com.example.recommend.sync;

import java.time.OffsetDateTime;

public record AdvertiserSelector(
        Long id,
        Long advertiserId,
        String role,        // "name" | "detail" | "price"
        String selector,
        Integer priority,
        Boolean enabled,
        String notes,
        OffsetDateTime createdAt,
        OffsetDateTime updatedAt
) {}
