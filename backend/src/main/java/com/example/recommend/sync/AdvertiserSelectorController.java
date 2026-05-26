package com.example.recommend.sync;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/advertisers/{advertiserId}/selectors")
public class AdvertiserSelectorController {

    private final AdvertiserSelectorRepository repo;

    public AdvertiserSelectorController(AdvertiserSelectorRepository repo) {
        this.repo = repo;
    }

    public record SelectorRequest(
            String role,        // "name" | "detail" | "price"
            String selector,
            Integer priority,
            Boolean enabled,
            String notes
    ) {}

    @GetMapping
    public List<AdvertiserSelector> list(@PathVariable long advertiserId) {
        return repo.findAll(advertiserId);
    }

    @PostMapping
    public AdvertiserSelector add(@PathVariable long advertiserId, @RequestBody SelectorRequest r) {
        long id = repo.insert(advertiserId, r.role(), r.selector(), r.priority(), r.enabled(), r.notes());
        return repo.findAll(advertiserId).stream().filter(s -> s.id() == id).findFirst().orElseThrow();
    }

    @PutMapping("/{id}")
    public ResponseEntity<Void> update(@PathVariable long advertiserId, @PathVariable long id, @RequestBody SelectorRequest r) {
        repo.update(id, r.selector(), r.priority(), r.enabled(), r.notes());
        return ResponseEntity.noContent().build();
    }

    @DeleteMapping("/{id}")
    public ResponseEntity<Void> delete(@PathVariable long advertiserId, @PathVariable long id) {
        repo.delete(id);
        return ResponseEntity.noContent().build();
    }

    /** 광고주의 selector 목록을 통째로 교체. 수정 폼에서 한 번에 저장할 때 사용. */
    public record BulkReplaceRequest(List<AdvertiserSelector> items) {}

    @PutMapping
    public ResponseEntity<List<AdvertiserSelector>> replace(@PathVariable long advertiserId,
                                                             @RequestBody BulkReplaceRequest r) {
        repo.replaceAll(advertiserId, r.items());
        return ResponseEntity.ok(repo.findAll(advertiserId));
    }
}
