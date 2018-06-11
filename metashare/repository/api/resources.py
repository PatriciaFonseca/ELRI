import datetime
from metashare.repository.api.auth import ApiDjangoAuthorization
from metashare.settings import DJANGO_URL
from metashare.storage.models import StorageObject
from tastypie.constants import ALL_WITH_RELATIONS, ALL
from tastypie.paginator import Paginator
from tastypie.resources import ModelResource
from tastypie.authentication import BasicAuthentication
from tastypie import fields
from tastypie.utils import trailing_slash
from haystack.query import SearchQuerySet
from django.conf.urls import url, include
from metashare.repository import models as lr


class IdentificationResource(ModelResource):
    class Meta:
        queryset = lr.identificationInfoType_model.objects.all()
        resource_name = 'identification'
        excludes = ['id', 'metaShareId']
        include_resource_uri = False

    resourceName = fields.DictField(attribute='resourceName')
    resourceShortName = fields.DictField(attribute='resourceShortName')
    description = fields.DictField(attribute='description')
    appropriatenessForDSI = fields.ListField(attribute='appropriatenessForDSI')
    identifier = fields.ListField(attribute='identifier')
    url = fields.ListField(attribute='url')

    def determine_format(self, request):
        return 'application/json'


class LicenceResource(ModelResource):
    class Meta:
        queryset = lr.licenceInfoType_model.objects.all()
        resource_name = "licence"
        allowed_methods = ['get']
        include_resource_uri = False

    licence = fields.CharField(attribute='licence')
    otherLicenceName = fields.CharField(attribute='otherLicenceName', null=True)
    otherLicence_TermsText = fields.DictField(attribute='otherLicence_TermsText', null=True)
    otherLicence_TermsURL = fields.CharField(attribute='otherLicence_TermsURL', null=True)
    restrictionsOfUse = fields.ListField(attribute='restrictionsOfUse', null=True)


class DistributionResource(ModelResource):
    class Meta:
        queryset = lr.distributionInfoType_model.objects.all()
        resource_name = "distribution"
        allowed_methods = ['get']
        include_resource_uri = False

    licenceInfo = fields.ToManyField(LicenceResource, 'licenceInfo', full=True)
    attributionText = fields.DictField(attribute='attributionText')
    distributionMedium = fields.ListField(attribute='distributionMedium')
    downloadLocation = fields.ListField(attribute='downloadLocation')
    executionLocation = fields.ListField(attribute='executionLocation')


class MetadataInfoResource(ModelResource):
    class Meta:
        queryset = lr.metadataInfoType_model.objects.all()
        resource_name = "metadataInfo"
        allowed_methods = ['get']
        fields = ['metadataCreationDate']
        include_resource_uri = False
        filtering = {
            'metadataCreationDate': ALL,
        }

    metadataCreationDate = fields.DateField(attribute='metadataCreationDate', verbose_name='created')

    def determine_format(self, request):
        return 'application/json'


pub_status = dict(p='published', g='ingested', i='internal')


class StorageResource(ModelResource):
    class Meta:
        queryset = StorageObject.objects.all()
        resource_name = 'storage'
        allowed_methods = ['get']
        include_resource_uri = False
        fields = ['identifier']

    def dehydrate(self, bundle):
        bundle.data['download_location'] = "{}/repository/download/{}".format(DJANGO_URL, bundle.obj.identifier)
        bundle.data['publication_status'] = pub_status.get(bundle.obj.publication_status)
        return bundle


class LrResource(ModelResource):
    class Meta:
        queryset = lr.resourceInfoType_model.objects.filter(storage_object__deleted=False) \
            .exclude(storage_object__publication_status='i')
        allowed_methods = ['get']
        resource_name = 'lr'
        authentication = BasicAuthentication()
        authorization = ApiDjangoAuthorization()
        # fields = ('identification', 'storage')
        # field_order = ('id', 'identification', 'storage')
        ordering = ['metadataInfo']
        filtering = {
            'metadataInfo': ALL_WITH_RELATIONS
        }
        # max_limit = None

    metadataInfo = fields.ToOneField(MetadataInfoResource, 'metadataInfo', null=True, full=True)
    identification = fields.ToOneField(IdentificationResource, 'identificationInfo', full=True)
    distribution = fields.ToManyField(DistributionResource, 'distributioninfotype_model_set', full=True)
    storage = fields.ToOneField(StorageResource, 'storage_object', full=True, null=True)

    def determine_format(self, request):
        return 'application/json'

    def prepend_urls(self):
        return [
            url(r"^(?P<resource_name>%s)/search%s$" % (self._meta.resource_name, trailing_slash()),
                self.wrap_view('get_search'), name="api_get_search"),
        ]

    def get_search(self, request, **kwargs):
        self.method_check(request, allowed=['get'])
        self.is_authenticated(request)
        self.dispatch_list(request)
        self.throttle_check(request)
        # Do the query.
        sqs = SearchQuerySet()
        query = request.GET.getlist("q")

        if query:
            query_dict = {}
        for q in query:
            try:
                query_dict[q.split(':')[0]] = q.split(':')[1]
                sqs = sqs.filter(**query_dict)
            except IndexError:
                sqs = sqs.filter(content=q)
        # Apply tastypie filters if any whatsoever
        sqs_objects = [sq.object for sq in sqs]
        filtered = self.apply_filters(request, applicable_filters={})

        final_list = list(set(sqs_objects) & set(filtered))
        ids = [fl.id for fl in final_list]
        final_list = lr.resourceInfoType_model.objects.filter(id__in=ids)
        if 'latest' in request.GET.get('sort', ''):
            final_list = self.apply_sorting(final_list, options={'sort': [u'latest']})
        elif 'earliest' in request.GET.get('sort', ''):
            final_list = self.apply_sorting(final_list, options={'sort': [u'earliest']})

        paginator = Paginator(request.GET, final_list, resource_uri='/api/v1/lr/search/')

        to_be_serialized = paginator.page()

        bundles = [self.build_bundle(obj=result, request=request) for result in to_be_serialized['objects']]
        to_be_serialized['objects'] = [self.full_dehydrate(bundle) for bundle in bundles]
        to_be_serialized = self.alter_list_data_to_serialize(request, to_be_serialized)
        return self.create_response(request, to_be_serialized)

    def apply_filters(self, request, applicable_filters):
        base_object_list = super(LrResource, self).apply_filters(request, applicable_filters)

        # custom filters
        span = request.GET.get('span', None)
        on = request.GET.get('on', None)
        on_before = request.GET.get('on_before', None)
        on_after = request.GET.get('on_after', None)
        before = request.GET.get('before', None)
        after = request.GET.get('after', None)

        filters = {}

        if span:
            filters.update(dict(metadataInfo__metadataCreationDate__range=span.split(',')))
        elif on:
            filters.update(dict(metadataInfo__metadataCreationDate__exact=on))
        elif on_before:
            filters.update(dict(metadataInfo__metadataCreationDate__lte=on_before))
        elif on_after:
            filters.update(dict(metadataInfo__metadataCreationDate__gte=on_after))
        elif before:
            filters.update(dict(metadataInfo__metadataCreationDate__lt=before))
        elif after:
            filters.update(dict(metadataInfo__metadataCreationDate__gt=after))

        return base_object_list.filter(**filters).distinct()

    def apply_sorting(self, obj_list, options=None):
        if options:
            if 'latest' in options.get('sort', ''):
                return obj_list.order_by('-metadataInfo__metadataCreationDate')
            elif 'earliest' in options.get('sort', ''):
                return obj_list.order_by('metadataInfo__metadataCreationDate')
        return super(LrResource, self).apply_sorting(obj_list, options)