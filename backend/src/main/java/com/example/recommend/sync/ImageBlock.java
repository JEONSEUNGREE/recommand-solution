package com.example.recommend.sync;

import java.time.OffsetDateTime;

public record ImageBlock(
        Long id,
        Long advertiserId,
        String pattern,
        Boolean enabled,
        String notes,
        OffsetDateTime createdAt,
        OffsetDateTime updatedAt
) {}
