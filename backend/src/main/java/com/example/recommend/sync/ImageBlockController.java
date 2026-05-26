package com.example.recommend.sync;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/advertisers/{advertiserId}/image-blocks")
public class ImageBlockController {

    private final ImageBlockRepository repo;

    public ImageBlockController(ImageBlockRepository repo) {
        this.repo = repo;
    }

    @GetMapping
    public List<ImageBlock> list(@PathVariable long advertiserId) {
        return repo.findAll(advertiserId);
    }

    public record BulkReplaceRequest(List<ImageBlock> items) {}
    public record AddRequest(String pattern, String notes) {}

    @PutMapping
    public ResponseEntity<List<ImageBlock>> replace(@PathVariable long advertiserId,
                                                     @RequestBody BulkReplaceRequest r) {
        repo.replaceAll(advertiserId, r.items());
        return ResponseEntity.ok(repo.findAll(advertiserId));
    }

    /** 단건 추가 — 이미지 모달에서 URL 패턴 즉시 추가 시 사용. */
    @PostMapping
    public ResponseEntity<List<ImageBlock>> add(@PathVariable long advertiserId, @RequestBody AddRequest r) {
        if (r.pattern() != null && !r.pattern().isBlank()) {
            List<ImageBlock> current = repo.findAll(advertiserId);
            current.add(new ImageBlock(null, advertiserId, r.pattern().trim(), true, r.notes(), null, null));
            repo.replaceAll(advertiserId, current);
        }
        return ResponseEntity.ok(repo.findAll(advertiserId));
    }

    /** 단건 삭제 — 모달에서 차단 해제할 때. */
    @DeleteMapping("/{id}")
    public ResponseEntity<Void> delete(@PathVariable long advertiserId, @PathVariable long id) {
        repo.delete(id);
        return ResponseEntity.noContent().build();
    }
}
