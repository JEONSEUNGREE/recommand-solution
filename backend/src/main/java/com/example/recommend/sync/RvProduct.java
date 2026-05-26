package com.example.recommend.sync;

import java.math.BigDecimal;
import java.time.OffsetDateTime;

public record RvProduct(
        Long id,
        Long advertiserId,
        String productCode,
        String productOriginCode,
        String productName,
        String companyNm,
        BigDecimal price,
        BigDecimal salePrice,
        String productUrl,
        String imageUrl,
        String bodyText,
        Integer bodyTextLen,
        String htmlPath,
        String imageDir,
        Integer imageLocalCount,
        String scrapeStatus,
        OffsetDateTime scrapedAt,
        String scrapeError,
        String enrichMethod,
        String enrichedInfo,
        OffsetDateTime enrichedAt,
        String enrichError
) {}
