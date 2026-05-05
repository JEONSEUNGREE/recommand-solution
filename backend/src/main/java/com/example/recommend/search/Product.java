package com.example.recommend.search;

public record Product(
        long id,
        String sku,
        String name,
        String brand,
        String category,
        String subcategory,
        String gender,
        String season,
        String style,
        String color,
        Integer price,
        Integer salePrice,
        Integer stock,
        String description,
        Double distance
) {}
