import endpoints
from google.appengine.ext import ndb
from datetime import datetime, timedelta
from endpoints_proto_datastore.ndb import EndpointsModel
from endpoints_proto_datastore.ndb import EndpointsAliasProperty
from endpoints_proto_datastore.ndb import EndpointsDateTimeProperty
from protorpc import remote
from protorpc import messages
import json
import sys
from googleapiclient import sample_tools
import httplib2
import os
from apiclient.discovery import build
from google.appengine.api import images
from google.appengine.api import urlfetch
import cloudstorage as gcs
import uuid
from google.appengine.api import app_identity
from google.appengine.ext import blobstore
from google.appengine.ext.ndb.query import ConjunctionNode
from google.appengine.api import search
my_default_retry_params = gcs.RetryParams(initial_delay=0.2,
                                          max_delay=5.0,
                                          backoff_factor=2,
                                          max_retry_period=15)
gcs.set_default_retry_params(my_default_retry_params)

DATETIME_STRING_FORMAT = '%Y-%m-%d %H:%M'
WEB_CLIENT_ID = '760560844994-04u6qkvpf481an26cnhkaauaf2dvjfk0.apps.googleusercontent.com'
DEV_CLIENT_ID = '760560844994-uiuqp0n00fns7q5nir8cduurrp8smcb3.apps.googleusercontent.com'
ANDROID_CLIENT_ID = '760560844994-7e0uo25vbdse175jc9tm0hgsra0bcllu.apps.googleusercontent.com'
IOS_CLIENT_ID = 'replace this with your iOS client ID'
ANDROID_AUDIENCE = WEB_CLIENT_ID
VALID_CLIENTS = [WEB_CLIENT_ID, DEV_CLIENT_ID, endpoints.API_EXPLORER_CLIENT_ID, ANDROID_CLIENT_ID]
QUERY_LIMIT_DEFAULT = 25
QUERY_LIMIT_MAX = 100
OFFER_FILTERED_FIELDS = ("id", "title", "subtitle", "image_urls", "categories", "lat", "lon")
MENTORING_FILTERED_FIELDS = ("id", "title", "image_url")
USER_FILTERED_FIELDS = ("im", "address", "name", "image_url")
PREVIEW_WIDTH = 152
PREVIEW_HEIGHT = 152
BUCKET_NAME = app_identity.get_default_gcs_bucket_name()
# hardcoded admins, useful for local development
admins = []

# property types
class LatLocationsFloatProperty(ndb.FloatProperty):
  def _validate(self, value):
    if not (-90 <= value <= 90):
      raise endpoints.BadRequestException('Value must be between -90 and 90, got %s' % value)

class LonLocationsFloatProperty(ndb.FloatProperty):
  def _validate(self, value):
    if not (-180 <= value <= 180):
      raise endpoints.BadRequestException('Value must be between -180 and 180, got %s' % value)

# models
class Category(EndpointsModel):
    _message_fields_schema = ('id', 'group', 'name','description')
    group = ndb.StringProperty(required=True)
    name = ndb.StringProperty(required=True)
    description = ndb.TextProperty(required=True)
    @EndpointsAliasProperty()
    def id(self):
        return self.key.urlsafe()

class FAQCategory(EndpointsModel):
    name = ndb.StringProperty(required=True)
    description = ndb.TextProperty(required=True)
    image = ndb.BlobProperty(repeated=False)
    image_url = ndb.StringProperty(repeated=False)
    blobkey = ndb.StringProperty(repeated=False)
    def IdSet(self, value):
        # By default, the property "id" assumes the "id" will be an integer in a
        # simple key -- e.g. ndb.Key(MyModel, 10) -- which is the default behavior
        # if no key is set. Instead, we wish to use a string value as the "id" here,
        # so first check if the value being set is a string.
        if not isinstance(value, basestring):
          raise TypeError('ID must be a string.')
        # We call UpdateFromKey, which each of EndpointsModel.IdSet and
        # EndpointsModel.EntityKeySet use, to update the current entity using a
        # datastore key. This method sets the key on the current entity, attempts to
        # retrieve a corresponding entity from the datastore and then patch in any
        # missing values if an entity is found in the datastore.
        key = ndb.Key(urlsafe=value)
        print(key)
        self.UpdateFromKey(ndb.Key(urlsafe=value))
    @EndpointsAliasProperty(setter=IdSet)
    def id(self):
        if self.key is not None:
            return self.key.urlsafe()

class User(EndpointsModel):
    user_id = ndb.StringProperty(indexed=True)
    im = ndb.StringProperty()
    address = ndb.StringProperty()
    interest = ndb.KeyProperty(Category, repeated=True)
    name = ndb.StringProperty(required=False)
    is_volunteer = ndb.BooleanProperty(default=False)
    is_admin = ndb.BooleanProperty(default=False)
    image = ndb.BlobProperty(repeated=False)
    image_url = ndb.StringProperty(repeated=False)
    blobkey = ndb.StringProperty(repeated=False)

class FAQItem(EndpointsModel):
    _message_fields_schema = ('id','question','answer','language','answered','category')
    question = ndb.TextProperty(required=True)
    answer = ndb.TextProperty(required=False)
    language = ndb.StringProperty(required=True)
    answered = ndb.BooleanProperty(default=False)
    category = ndb.KeyProperty(FAQCategory, repeated=False)
    owner_key = ndb.KeyProperty(User)


class Offer(EndpointsModel):
    _message_fields_schema = ('id', 'title', 'subtitle', 'description', 'categories',
        'image_urls', 'lat','lon', 'owner', 'end_date', 'owner_key')
    creation_date = ndb.DateTimeProperty(auto_now_add=True)
    title = ndb.StringProperty(required=True)
    subtitle = ndb.StringProperty(required=True)
    description = ndb.StringProperty(required=True)
    lat = LatLocationsFloatProperty(required=False)
    lon = LonLocationsFloatProperty(required=False)
    categories = ndb.KeyProperty(Category, repeated=True)
    end_date = EndpointsDateTimeProperty(auto_now_add=False, required=True, string_format=DATETIME_STRING_FORMAT) # yyyy-mm-dd hh:mm
    images = ndb.BlobProperty(repeated=True)
    image_urls = ndb.StringProperty(repeated=True)
    blobkeys = ndb.StringProperty(repeated=True)
    bbox = ndb.StringProperty(required=False)
    owner_key = ndb.KeyProperty(User)
    """
    bbox = left,bottom,right,top
    bbox = min Longitude , min Latitude , max Longitude , max Latitude
    """
    @EndpointsAliasProperty(property_type=User.ProtoModel())
    def owner(self):
        if self.owner_key != None:
            owner = self.owner_key.get()
            cleaned_owner = User(name = owner.name, im = owner.im, interest = owner.interest)
            return cleaned_owner

class MentoringRequest(EndpointsModel):
    _message_fields_schema = ('id', 'title', 'description',
        'image_url', 'lat','lon', 'requester', 'end_date', 'requester_key')
    creation_date = ndb.DateTimeProperty(auto_now_add=True)
    title = ndb.StringProperty(required=True)
    description = ndb.StringProperty(required=True)
    lat = LatLocationsFloatProperty(required=False)
    lon = LonLocationsFloatProperty(required=False)
    end_date = EndpointsDateTimeProperty(auto_now_add=False, required=True, string_format=DATETIME_STRING_FORMAT) # yyyy-mm-dd hh:mm
    image = ndb.BlobProperty(repeated=False)
    image_url = ndb.StringProperty(repeated=False)
    blobkey = ndb.StringProperty(repeated=False)
    bbox = ndb.StringProperty(required=False)
    requester_key = ndb.KeyProperty(User)
    @EndpointsAliasProperty(property_type=User.ProtoModel())
    def requester(self):
        if self.requester_key != None:
            requester = self.requester_key.get()
            cleaned_requester = User(name = requester.name, im = requester.im, interest = requester.interest)
            return cleaned_requester
class SearchRequest(messages.Message):
  query = messages.StringField(1)
  limit = messages.IntegerField(2)
  offset = messages.IntegerField(3)
# api v1
@endpoints.api(name='donate', version='v1',audiences=VALID_CLIENTS, allowed_client_ids=VALID_CLIENTS,
    scopes=[endpoints.EMAIL_SCOPE, "https://www.googleapis.com/auth/plus.login"])
class DonateApi(remote.Service):
    """Donate API v1."""
    def get_user_id(self,user):
        if user.user_id() != None:
            return user.user_id()
        else:
            return user.nickname()

    def get_current_user(self):
        current_user = endpoints.get_current_user()
        user_id = self.get_user_id(current_user)
        users = User.query(User.user_id == user_id)
        if users.count() != 1:
            raise endpoints.NotFoundException("There is no user with user id %s. Users need to be registered first." % user_id)
        return users.get()
    def is_current_user_admin(self):
        current_user = endpoints.get_current_user()
        email = current_user.email()
        try:
            if email in admins:
                return True
            if self.get_current_user().is_admin:
                return True
        except:
            return False
        return False

    @Offer.method(path='offer', http_method='POST', name='offer.create',user_required=True,
        request_fields=('title', 'subtitle', 'description', 'categories', 'images', 'lat','lon', 'end_date'))
    def OfferInsert(self, offer):
        """ Created create offer"""
        user = self.get_current_user()
        offer.owner_key = user.key
        urls = []
        blobkeys = []
        for image in offer.images:
            if len(image) > 6*1024*1024:
                for blobkey in blobkeys:
                    gcs.delete(blobkey)
                raise endpoints.BadRequestException("Max. image size is 6*1024*1024 bytes")
            write_retry_params = gcs.RetryParams(backoff_factor=1.1)
            filename = "/" + BUCKET_NAME + "/" +str(uuid.uuid4())
            png = images.rotate(image, 0, output_encoding=images.PNG)
            gcs_file = gcs.open(filename,'w',retry_params=write_retry_params,content_type='image/png',)
            gcs_file.write(image)
            gcs_file.close()
            blobkey = blobstore.create_gs_key("/gs" + filename)
            blobkeys.append(filename)
            #url = images.get_serving_url("gs" + filename)
            url = images.get_serving_url(blobkey)
            urls.append(url)
        offer.image_urls = urls
        offer.blobkeys = blobkeys
        del offer.images
        offer.put()
        return offer
    @Offer.query_method(user_required=False, path='offers_near', name='offer.list_near',
        query_fields=("bbox",'limit', 'order', 'pageToken'),
        collection_fields=OFFER_FILTERED_FIELDS,
        limit_default=QUERY_LIMIT_DEFAULT,limit_max=QUERY_LIMIT_MAX)
    def NearOfferList(self, data):
        """Returns #limit Offers in bbox"""
        if data.filters != None:
            bbox = data.filters._FilterNode__value
            bbox = bbox.split(",")
            if bbox[0] != bbox[2] and bbox[1] != bbox[3]:
                qry = Offer.query(ndb.AND(Offer.lon > float(bbox[0]), Offer.lon < float(bbox[2])))
                qry.filter(Offer.end_date > datetime.now()).filter(ndb.AND(Offer.lat > float(bbox[1]),
                    Offer.lat < float(bbox[3])))
                return qry
            else:
                raise endpoints.BadRequestException("The area of the bbox needs to be larger than 0")
        else:
            raise endpoints.BadRequestException("bbox value is needed")

    @Offer.query_method(user_required=True, path='offers_by_user', name='offer.byuser',
        query_fields=("owner_key", "limit", 'pageToken'),
        collection_fields=OFFER_FILTERED_FIELDS,
        limit_default=QUERY_LIMIT_DEFAULT,limit_max=QUERY_LIMIT_MAX)
    def OfferByUser(self, query):
        """Gets top #limit offers by user"""
        query = query.filter(Offer.end_date > datetime.now()).order(Offer.end_date)
        return query

    @Offer.query_method(user_required=False, path='offers_by_cat', name='offer.bycat',
        query_fields=("bbox","categories", "limit", 'pageToken'),
        collection_fields=OFFER_FILTERED_FIELDS,
        limit_default=QUERY_LIMIT_DEFAULT,limit_max=QUERY_LIMIT_MAX)
    def OfferByCat(self, data):
        """ Gets top #limit offers by cat"""
        bb = ""
        if type(data.filters) == ConjunctionNode:
            for i in data.filters._ConjunctionNode__nodes:
                if i._FilterNode__name == "bbox":
                    bb = i
                if i._FilterNode__name == "categories":
                    cat = i
            if bb != "":
                bbox = bb._FilterNode__value
                bbox = bbox.split(",")
                if bbox[0] != bbox[2] and bbox[1] != bbox[3]:
                    qry = Offer.query(cat)
                    qry.filter(ndb.AND(Offer.lon > float(bbox[0]), Offer.lon < float(bbox[2])))
                    qry.filter(Offer.end_date > datetime.now()).filter(ndb.AND(Offer.lat > float(bbox[1]),
                        Offer.lat < float(bbox[3])))
                    return qry
                else:
                    raise endpoints.BadRequestException("The area of the bbox needs to be larger than 0")
            else:
                raise endpoints.BadRequestException("bbox value is needed")
        else:
            raise endpoints.BadRequestException("you need to search for bbox and categories")

    @Offer.method(http_method='GET', user_required=False, request_fields=('id',),
                      path='offer/{id}', name='offer.get', response_fields=('id', 'title', 'subtitle', 'description', 'categories',
        'image_urls', 'lat','lon', 'owner', 'end_date', 'owner_key'))
    def OfferGet(self, offer):
        """ Gets offer details by offer id"""
        if not offer.from_datastore:
            raise endpoints.NotFoundException('Offer not found.')
        return offer

    @Offer.method(user_required=True, path='delete_offer', name='offer.delete',
        request_fields=("id",), http_method="POST")
    def DeleteOffer(self, query):
        """Deletes offer"""
        current_user = endpoints.get_current_user()
        if query.from_datastore is True:
            user_id = query.owner_key.get().user_id
            if user_id == self.get_user_id(current_user):
                for blobkey in query.blobkeys:
                    gcs.delete(blobkey)
                print(query.key.delete())
                return query
            else:
                raise endpoints.UnauthorizedException("You can only delete your own offers.")
        else:
            raise endpoints.NotFoundException("Offer not found.")

    @User.method(path='create_user', http_method='POST', name='user.create',user_required=True,
        request_fields=())
    def UserInsert(self, user):
        """ Creates create user"""
        current_user = endpoints.get_current_user()
        user_id = self.get_user_id(current_user)
        users = User.query(User.user_id == user_id)
        if users.count() == 0:
            user.user_id = user_id
            if user.image != None:
                image = user.image
                if len(image) > 6*1024*1024:
                    raise endpoints.BadRequestException("Max. image size is 6*1024*1024 bytes")
                write_retry_params = gcs.RetryParams(backoff_factor=1.1)
                filename = "/" + BUCKET_NAME + "/" +str(uuid.uuid4())
                png = images.rotate(image, 0, output_encoding=images.PNG)
                gcs_file = gcs.open(filename,'w',retry_params=write_retry_params,content_type='image/png',)
                gcs_file.write(png)
                gcs_file.close()
                blobkey = blobstore.create_gs_key("/gs" + filename)
                url = images.get_serving_url(blobkey)
                user.image_url = url
                user.blobkey = filename
            del user.image
            """TODO: write better code; temp fix to get users name"""
            headers = {'Authorization': os.getenv('HTTP_AUTHORIZATION')}
            url = "https://www.googleapis.com/plus/v1/people/me"
            result = urlfetch.fetch(url=url,
            method=urlfetch.GET,
            headers=headers)

            profile = json.loads(result.content)
            print(profile)
            user.name = profile['displayName']
            profUrl = profile['url']
            if user.im != None:
                try:
                    im_json = json.loads(user.im)
                except:
                    raise endpoints.BadRequestException("im needs to be empty or valid json")
            else:
                im_json = {}
            im_json["gplus"] = {"url":profUrl,"display":user.name}
            user.im = json.dumps(im_json)
            current_user = endpoints.get_current_user()
            email = current_user.email()
            if self.is_current_user_admin():
                user.is_volunteer = True
                user.is_admin = True
            else:
                user.is_volunteer = False
                user.is_admin = False
            user.put()
            print(user)
            return user
        else:
            return users.get()

    @User.method(path='update_user', http_method='POST', name='user.update',user_required=True,
        request_fields=('address', 'im', 'interest', 'image'), response_fields=USER_FILTERED_FIELDS)
    def UserUpdate(self, user):
        """ Updates informations about the user """
        current_user = endpoints.get_current_user()

        user_id = self.get_user_id(current_user)

        users = User.query(User.user_id == user_id)

        if users.count() != 1:
            raise endpoints.BadRequestException("There no user with user id %s." % user_id)
        else:
            update_user = users.get()
            if user.image != None:
                image = user.image
                if len(image) > 6*1024*1024:
                    raise endpoints.BadRequestException("Max. image size is 6*1024*1024 bytes")
                write_retry_params = gcs.RetryParams(backoff_factor=1.1)
                filename = "/" + BUCKET_NAME + "/" +str(uuid.uuid4())
                png = images.rotate(image, 0, output_encoding=images.PNG)
                gcs_file = gcs.open(filename,'w',retry_params=write_retry_params,content_type='image/png',)
                gcs_file.write(png)
                gcs_file.close()
                blobkey = blobstore.create_gs_key("/gs" + filename)
                url = images.get_serving_url(blobkey)
                update_user.image_url = url
                update_user.blobkey = filename
            del user.image
            update_user.address = user.address
            if user.im != None:
                try:
                    im_json = json.loads(user.im)
                except:
                    raise endpoints.BadRequestException("im needs to be empty or valid json")
            else:
                im_json = {}
            if "gplus" in json.loads(update_user.im):
                im_json["gplus"] = json.loads(update_user.im)["gplus"]
            else:
                """TODO: write better code; temp fix to get users name"""
                headers = {'Authorization': os.getenv('HTTP_AUTHORIZATION')}
                url = "https://www.googleapis.com/plus/v1/people/me"
                result = urlfetch.fetch(url=url,
                method=urlfetch.GET,
                headers=headers)
                profile = json.loads(result.content)
                user.name = profile['displayName']
                profUrl = profile['url']
                im_json["gplus"] = {"url":profUrl,"display":user.name}
            update_user.im = json.dumps(im_json)
            if user.interest != None:
                update_user.interest = user.interest
            update_user.put()
            return update_user

    @User.method(http_method='GET', user_required=True, path='user_data', name='user.data',
        request_fields=(), response_fields=USER_FILTERED_FIELDS)
    def UserData(self, user):
        """Gets the userdata"""
        current_user = endpoints.get_current_user()
        user = User.query(User.user_id == self.get_user_id(current_user))
        if user.count() == 1:
            return user.get()
        else:
            raise endpoints.NotFoundException("User not found")

    @Category.method(path='cat', http_method='POST', name='cat.create',user_required=True,
        request_fields=("group","name","description"))
    def CategoryInsert(self, cat):
        """ Creates create category (only limited users)"""
        current_user = endpoints.get_current_user()
        email = current_user.email()
        if self.is_current_user_admin():
            cat.put()
            return cat
        else:
            raise endpoints.UnauthorizedException("Only Admin users can create Categories. \
                Contact donate@ca.pajowu.de for more information.")

    @Category.query_method(user_required=False, path='cats', name='cat.list', limit_default=100)
    def CategoryList(self, query):
        """Returns all categories"""
        return query

    @FAQCategory.method(path='faqcat', http_method='POST', name='faqcat.create',user_required=True,
        request_fields=("name","description"))
    def FAQCategoryInsert(self, cat):
        """ Creates create category (only limited users)"""
        current_user = endpoints.get_current_user()
        email = current_user.email()
        if self.is_current_user_admin():
            cat.put()
            return cat
        else:
            raise endpoints.UnauthorizedException("Only Admin users can create Categories. \
                Contact donate@ca.pajowu.de for more information.")

    @FAQCategory.query_method(user_required=False, path='faqcats', name='faqcat.list', limit_default=100)
    def FAQCategoryList(self, query):
        """Returns all categories"""
        return query

    @FAQCategory.method(path='faqcat_update', http_method='POST', name='faqcat.update',user_required=True,
        request_fields=('id','image'))
    def FAQCategoryUpdate(self, faqcat):
        """ update faqcat"""
        if self.is_current_user_admin():
            print(faqcat.id)
            if faqcat.id != None:
                item = ndb.Key(urlsafe=faqcat.id).get()
                if item is None:
                    raise endpoints.BadRequestException("FAQCategory not found")
                else:
                    if faqcat.image != None:
                        image = faqcat.image
                        if len(image) > 6*1024*1024:
                            raise endpoints.BadRequestException("Max. image size is 6*1024*1024 bytes")
                        write_retry_params = gcs.RetryParams(backoff_factor=1.1)
                        filename = "/" + BUCKET_NAME + "/" +str(uuid.uuid4())
                        png = images.rotate(image, 0, output_encoding=images.PNG)
                        gcs_file = gcs.open(filename,'w',retry_params=write_retry_params,content_type='image/png',)
                        gcs_file.write(png)
                        gcs_file.close()
                        blobkey = blobstore.create_gs_key("/gs" + filename)
                        url = images.get_serving_url(blobkey)
                        item.image_url = url
                        item.blobkey = filename
                    del faqcat.image
                    item.put()
                    return item
            else:
                raise endpoints.BadRequestException("ID missing")
        else:
            raise endpoints.UnauthorizedException("Only Volunteers users can update FAQCategory. \
                Contact donate@ca.pajowu.de for more information.")

    @FAQItem.method(path='faqitem/create', http_method='POST', name='faqitem.create',user_required=True,
        request_fields=('question','answer','language','answered','category'))
    def FAQItemInsert(self, faqitem):
        """ Create new faqitem"""
        index = search.Index(name='faqitems')

        user = self.get_current_user()
        faqitem.owner_key = user.key
        faqitem.answered = False
        faqitem.put()
        fields = [search.TextField(name='question', value=faqitem.question),
            search.TextField(name='answer', value=faqitem.answer)]
        d = search.Document(doc_id=str(faqitem.id), fields=fields, language=faqitem.language)
        index.put(d)
        return faqitem

    @FAQItem.method(path='faqitem/update', http_method='POST', name='faqitem.update',user_required=True,
        request_fields=('id','question','answer','language','answered','category'))
    def FAQItemUpdate(self, faqitem):
        """ Created create faqitem"""
        user = self.get_current_user()
        if user.is_volunteer:
            if faqitem.id != None:
                item = FAQItem.get_by_id(faqitem.id)
                if item is None:
                    raise endpoints.BadRequestException("FAQItem not found")
                else:
                    item.question = faqitem.question
                    item.answered = faqitem.answered
                    item.answer = faqitem.answer
                    item.language = faqitem.language
                    item.category = faqitem.category
                    item.put()
                    return item
            else:
                raise endpoints.BadRequestException("ID missing")
        else:
            raise endpoints.UnauthorizedException("Only Volunteers users can update FAQItems. \
                Contact donate@ca.pajowu.de for more information.")

    @FAQItem.query_method(user_required=True, path='faqitems/by_user', name='faqitem.byuser',
        query_fields=("owner_key", "limit", 'pageToken'),
        limit_default=QUERY_LIMIT_DEFAULT,limit_max=QUERY_LIMIT_MAX)
    def FAQItemByUser(self, query):
        """Gets top #limit faqitems by user"""
        return query

    @FAQItem.query_method(user_required=False, path='faqitems/by_cat', name='faqitem.bycat',
        query_fields=("category", "answered","limit", 'pageToken'),
        limit_default=QUERY_LIMIT_DEFAULT,limit_max=QUERY_LIMIT_MAX, collection_fields=('question','answer','language'))
    def FAQItemByCat(self, data):
        """ Gets top #limit faqitems by cat"""
        """
        for filter in data.filtes:
            if filter._FilterNode__name == "answered":
                return data
        return data.filter(FAQItem.answered == True)"""
        return data

    @FAQItem.method(http_method='GET', user_required=False, request_fields=('id',),
                      path='faqitem/get/{id}', name='faqitem.get')
    def FAQItemGet(self, faqitem):
        """ Gets faqitem details by faqitem id"""
        if not faqitem.from_datastore:
            raise endpoints.NotFoundException('FAQItem not found.')
        return faqitem
    @FAQItem.query_method(user_required=False, path='faqitem/list', name='faqitem.list', limit_default=100, query_fields=("answered", "limit", 'pageToken'))
    def FAQItemList(self, query):
        """Returns all categories"""
        return query
    @FAQItem.method(user_required=True, path='faqitem/delete', name='faqitem.delete',
        request_fields=("id",), http_method="POST")
    def DeleteFAQItem(self, query):
        """Deletes faqitem"""
        current_user = endpoints.get_current_user()
        if query.from_datastore is True:
            user_id = query.owner_key.get().user_id
            if (user_id == self.get_user_id(current_user)) or query.owner_key.get().is_volunteer:
                query.key.delete()
                return query
            else:
                raise endpoints.UnauthorizedException("You can only delete your own faqitems.")
        else:
            raise endpoints.NotFoundException("FAQItem not found.")
    @endpoints.method(SearchRequest, FAQItem.ProtoCollection(),
                    path='faqitem/search', http_method='POST',
                    name='faqitem.search')
    def SearchFAQItem(self, request):
        if request.query is not None:
            index = search.Index(name='faqitems')
            limit = min(request.limit, QUERY_LIMIT_MAX) if min(request.limit, QUERY_LIMIT_MAX) > 0 else 1
            search_query = search.Query(
              query_string=request.query,
              options=search.QueryOptions(
                    ids_only=True,
                    offset=max(request.offset,0),
                    limit = limit))
            search_results = index.search(search_query)
            if search_results.number_found == 0:
                return FAQItem.ToMessageCollection([])
            else:
                document_ids = [ndb.Key('FAQItem', int(document.doc_id))
                        for document in search_results.results]
                query = FAQItem.query(ndb.AND(
                                        FAQItem.key.IN(document_ids),
                                        FAQItem.answered == True)
                                    )
                return FAQItem.ToMessageCollection(query.fetch())

        else:
            raise endpoints.BadRequestException("Search query missing")

    @MentoringRequest.method(path='mentoringrequest', http_method='POST', name='mentoringrequest.create',user_required=True,
        request_fields=('title', 'description', 'image', 'lat','lon', 'end_date'))
    def MentoringRequestInsert(self, mentoringrequest):
        """ Create mentoringrequest"""

        user = self.get_current_user()
        mentoringrequest.requester_key = user.key
        if mentoringrequest.image != None:
            image = mentoringrequest.image
            if len(image) > 6*1024*1024:
                raise endpoints.BadRequestException("Max. image size is 6*1024*1024 bytes")
            write_retry_params = gcs.RetryParams(backoff_factor=1.1)
            filename = "/" + BUCKET_NAME + "/" +str(uuid.uuid4())
            png = images.rotate(image, 0, output_encoding=images.PNG)
            gcs_file = gcs.open(filename,'w',retry_params=write_retry_params,content_type='image/png',)
            gcs_file.write(png)
            gcs_file.close()
            blobkey = blobstore.create_gs_key("/gs" + filename)
            url = images.get_serving_url(blobkey)
            mentoringrequest.image_url = url
            print(url)
            mentoringrequest.blobkey = filename
            print(blobkey)
        del mentoringrequest.image
        mentoringrequest.put()
        return mentoringrequest
    @MentoringRequest.query_method(user_required=True, path='mentoringrequests_near', name='mentoringrequest.list_near',
        query_fields=("bbox",'limit', 'order', 'pageToken'),
        collection_fields=MENTORING_FILTERED_FIELDS,
        limit_default=QUERY_LIMIT_DEFAULT,limit_max=QUERY_LIMIT_MAX)
    def NearMentoringRequestList(self, data):
        """Returns #limit MentoringRequests in bbox"""
        current_user = self.get_current_user();
        if (current_user.is_volunteer or current_user.is_admin):
            if data.filters != None:
                bbox = data.filters._FilterNode__value
                bbox = bbox.split(",")
                if bbox[0] != bbox[2] and bbox[1] != bbox[3]:
                    qry = MentoringRequest.query(ndb.AND(MentoringRequest.lon > float(bbox[0]), MentoringRequest.lon < float(bbox[2])))
                    qry.filter(MentoringRequest.end_date > datetime.now()).filter(ndb.AND(MentoringRequest.lat > float(bbox[1]),
                        MentoringRequest.lat < float(bbox[3])))
                    return qry
                else:
                    raise endpoints.BadRequestException("The area of the bbox needs to be larger than 0")
            else:
                raise endpoints.BadRequestException("bbox value is needed")
        else:
            raise endpoints.BadRequestException("Only volunteers can search MentoringRequests")


    @MentoringRequest.query_method(user_required=True, path='mentoringrequests_by_user', name='mentoringrequest.byuser',
        query_fields=("requester_key", "limit", 'pageToken'),
        collection_fields=MENTORING_FILTERED_FIELDS,
        limit_default=QUERY_LIMIT_DEFAULT,limit_max=QUERY_LIMIT_MAX)
    def MentoringRequestByUser(self, query):
        """Gets top #limit mentoringrequests by user"""
        current_user = self.get_current_user();
        if (current_user.is_admin):
            query = query.filter(MentoringRequest.end_date > datetime.now()).order(MentoringRequest.end_date)
            return query
        else:
            raise endpoints.BadRequestException("Only volunteers can search MentoringRequests")


    @MentoringRequest.method(http_method='GET', user_required=True, request_fields=('id',),
                      path='mentoringrequest/{id}', name='mentoringrequest.get', response_fields=('id', 'title', 'description', 'image_url', 'lat','lon', 'requester', 'end_date'))
    def MentoringRequestGet(self, mentoringrequest):
        """ Gets mentoringrequest details by mentoringrequest id"""
        current_user = self.get_current_user();
        if (current_user.is_volunteer or current_user.is_admin):
            if not mentoringrequest.from_datastore:
                raise endpoints.NotFoundException('MentoringRequest not found.')
            return mentoringrequest
        else:
            raise endpoints.BadRequestException("Only volunteers can search MentoringRequests")

    @MentoringRequest.method(user_required=True, path='delete_mentoringrequest', name='mentoringrequest.delete',
        request_fields=("id",), http_method="POST")
    def DeleteMentoringRequest(self, query):
        """Deletes mentoringrequest"""
        current_user = endpoints.get_current_user()
        if query.from_datastore is True:
            user_id = query.requester_key.get().user_id
            if user_id == self.get_user_id(current_user):
                gcs.delete(query.blobkey)
                print(query.key.delete())
                return query
            else:
                raise endpoints.UnauthorizedException("You can only delete your own mentoringrequests.")
        else:
            raise endpoints.NotFoundException("MentoringRequest not found.")

application = endpoints.api_server([DonateApi], restricted=False)