"""Step 4: an approved version is created in the store, hidden and disabled, and verified.

The chosen image comes back from where it waits (nothing is stored, decision #36):
image 1 from the owner's email (guarded and postprocessed again), image 2 from the agent's own
sent preview email. It goes to the private bucket only for the upload, and is deleted after.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from autolist.channels.email.mail_sender import MAX_OUT_ATTACHMENT_BYTES
from autolist.channels.email.own_attachment import fetch_own_attachment
from autolist.core.facts import store_model
from autolist.core.models import Job, JobState, ProductDraft
from autolist.core.playbook import require
from autolist.core.sale import sale_spec
from autolist.core.state_machine import transition
from autolist.images.postprocess import postprocess
from autolist.orchestrator.generate_step import job_photos
from autolist.orchestrator.runtime import Runtime, product_name, send, to_owner
from autolist.publish.publisher import create_hidden
from autolist.review.preview import IMAGE_NAMES, LIFESTYLE_NAME


def _fact(job: Job, key: str) -> str:
    fact = job.fact(key)
    if fact is None:
        raise RuntimeError(f"{job.job_id}: no {key} fact at publish")
    return fact.value


def _store_model(job: Job) -> str:
    model = store_model(job)
    if model is None:
        raise RuntimeError(f"{job.job_id}: no model or item code at publish")
    return model


def _image(rt: Runtime, job: Job) -> bytes:
    version = job.versions[-1]
    if version.image_choice == 2:
        if version.preview_gmail_id is None:
            raise RuntimeError(f"{job.job_id}: image 2 chosen but the preview id is missing")
        return fetch_own_attachment(
            rt.gmail(),
            version.preview_gmail_id,
            IMAGE_NAMES[2],
            config=rt.config.channels.email,
            max_bytes=MAX_OUT_ATTACHMENT_BYTES,
        )
    px: int = rt.config.require("images.output_px")
    return postprocess(job_photos(rt, job)[0].data, px)


def _lifestyle(rt: Runtime, job: Job) -> bytes | None:
    """The lifestyle image of the approved version, from its own preview email."""
    version = job.versions[-1]
    if not version.lifestyle:
        return None
    if version.preview_gmail_id is None:
        raise RuntimeError(f"{job.job_id}: lifestyle attached but the preview id is missing")
    return fetch_own_attachment(
        rt.gmail(),
        version.preview_gmail_id,
        LIFESTYLE_NAME,
        config=rt.config.channels.email,
        max_bytes=MAX_OUT_ATTACHMENT_BYTES,
    )


def draft_of(rt: Runtime, job: Job, image: bytes, lifestyle: bytes | None = None) -> ProductDraft:
    version = job.versions[-1]
    try:
        price = Decimal(_fact(job, "price"))
        quantity = int(_fact(job, "quantity"))
    except (InvalidOperation, ValueError) as exc:
        raise RuntimeError(f"{job.job_id}: price or quantity is not a number") from exc
    barcode = job.fact("barcode")
    item_code = job.fact("item_code")
    try:
        special = sale_spec(job, rt.today())  # starts on the publish day (DEV 15.11.2026)
    except ValueError as exc:
        raise RuntimeError(f"{job.job_id}: sale not valid at publish: {exc}") from exc
    ref = rt.blobs.put(f"{job.job_id}/v{version.number}-main.jpg", image, "image/jpeg")
    extra = (
        [rt.blobs.put(f"{job.job_id}/v{version.number}-lifestyle.jpg", lifestyle, "image/jpeg")]
        if lifestyle is not None
        else []
    )
    return ProductDraft(
        job_id=job.job_id,
        name=version.title,
        description_html=version.description_html,
        price=price,
        quantity=quantity,
        sku=item_code.value if item_code else None,  # B-03; None: not sent (decision 15.11)
        model=_store_model(job),
        upc=barcode.value if barcode else None,
        category_ids=version.category_ids,
        stock_managed=bool(require(rt.playbook, "stock.subtract")),  # DEV 15.11: always on
        main_image=ref,
        extra_images=extra,
        special=special,
    )


async def publish(rt: Runtime, job: Job) -> Job:
    languages: list[object] = rt.config.require("istores.required_name_languages")
    page_size: int = rt.config.require("istores.catalog_page_size")
    draft = draft_of(rt, job, _image(rt, job), _lifestyle(rt, job))
    try:
        async with rt.istores() as client:
            product_id = await create_hidden(
                draft,
                config=rt.config,
                client=client,
                blobs=rt.blobs,
                language_id=int(str(languages[0])),
                page_size=page_size,
            )
    finally:
        for ref in [draft.main_image, *draft.extra_images]:
            rt.blobs.delete(ref)
    job = job.model_copy(update={"istores_product_id": product_id})
    job = transition(job, JobState.SAVED_HIDDEN, rt.now())
    rt.jobs.save(job)
    _, saved = send(
        rt,
        to_owner(
            "saved_hidden",
            {
                "job_id": job.job_id,
                "product_name": product_name(job),
                "version": str(job.versions[-1].number),
                "istores_product_id": str(product_id),
            },
            to=rt.owner,
            thread_id=job.thread_id,
        ),
        job,
    )
    return saved or job
